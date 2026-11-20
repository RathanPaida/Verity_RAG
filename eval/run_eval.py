"""Evaluate retrieval (and optionally the full agent) against the golden set.

    python -m eval.run_eval --modes vector,text,hybrid --k 5            # retrieval comparison
    python -m eval.run_eval --agent --judge                              # + citations, abstention, faithfulness, cost
    python -m eval.run_eval --agent --save-baseline                      # write eval/baseline.json
    python -m eval.run_eval --agent --gate                               # CI: exit 1 on regression vs baseline
    python -m eval.run_eval --ingest data/sample                         # ingest first (e.g. mongomock:// in CI)

Results are printed and stored in `eval_runs` so retrieval changes can be compared over time.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from verity import db
from verity.config import settings
from verity.retrieval.hybrid import Filters, backends, hybrid_search

from .metrics import citation_precision, hit_at_k, mean, mrr, ndcg_at_k, percentile, recall_at_k, source_key

HERE = Path(__file__).parent
GOLDEN = HERE / "golden.jsonl"
BASELINE = HERE / "baseline.json"

# metric -> (direction, tolerated regression)
GATES = {
    "hybrid.recall": ("higher", 0.03),
    "hybrid.mrr": ("higher", 0.05),
    "agent.abstention_accuracy": ("higher", 0.0),
    "agent.citation_precision": ("higher", 0.05),
    "agent.faithfulness": ("higher", 0.05),
}


def load_golden(path: Path = GOLDEN) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def eval_retrieval(engagement_id: str, mode: str, k: int, golden: list[dict]) -> dict:
    rows, lat = [], []
    for g in golden:
        if not g["answerable"]:
            continue
        t0 = time.perf_counter()
        hits = hybrid_search(g["question"], Filters(engagement_id), k=k * 2, mode=mode)
        lat.append((time.perf_counter() - t0) * 1000)
        got = [source_key(h.source_id) for h in hits]
        rows.append({"id": g["id"], "recall": recall_at_k(got, g["expected_sources"], k),
                     "hit": hit_at_k(got, g["expected_sources"], k), "mrr": mrr(got, g["expected_sources"]),
                     "ndcg": ndcg_at_k(got, g["expected_sources"], k), "retrieved": got[:k]})
    return {"mode": mode, "k": k, "backend": backends(),
            "recall": mean([r["recall"] for r in rows]), "hit": mean([r["hit"] for r in rows]),
            "mrr": mean([r["mrr"] for r in rows]), "ndcg": mean([r["ndcg"] for r in rows]),
            "p50_ms": percentile(lat, 50), "p95_ms": percentile(lat, 95), "per_question": rows}


def eval_agent(engagement_id: str, golden: list[dict], judge: bool = False) -> dict:
    from verity.service import ask

    from .judge import faithfulness

    rows = []
    for g in golden:
        try:
            r = ask(g["question"], engagement_id, use_cache=False)
        except Exception as e:  # one failed question must not abort the whole evaluation
            rows.append({"id": g["id"], "status": "error", "error": str(e)[:300], "abstention_correct": False,
                         "citation_precision": float("nan"), "latency_ms": float("nan"), "cost_usd": 0.0,
                         "tokens_out_per_sec": 0.0, "run_id": None})
            continue
        cited = [source_key(c["source_id"]) for c in r["citations"]]
        abstained = r["status"] != "answered"
        row = {"id": g["id"], "status": r["status"], "abstention_correct": abstained == (not g["answerable"]),
               "citation_precision": citation_precision(cited, g["expected_sources"]) if g["answerable"]
               else float("nan"),
               "latency_ms": r["metrics"]["latency_ms"], "cost_usd": r["metrics"]["cost_usd"],
               "tokens_out_per_sec": r["metrics"]["output_tokens_per_sec"], "run_id": r["run_id"]}
        if judge and r["status"] == "answered":
            f = faithfulness(r["answer"], r["citations"])
            row["faithfulness"], row["unsupported"] = f["score"], f["unsupported"]
        rows.append(row)
    return {"abstention_accuracy": mean([1.0 if r["abstention_correct"] else 0.0 for r in rows]),
            "citation_precision": mean([r["citation_precision"] for r in rows]),
            "faithfulness": mean([r.get("faithfulness", float("nan")) for r in rows]),
            "p50_latency_ms": percentile([r["latency_ms"] for r in rows if r["status"] != "error"], 50),
            "p95_latency_ms": percentile([r["latency_ms"] for r in rows if r["status"] != "error"], 95),
            "errors": sum(r["status"] == "error" for r in rows),
            "mean_cost_usd": mean([r["cost_usd"] for r in rows]), "per_question": rows}


def flatten(report: dict) -> dict[str, float]:
    out = {}
    for r in report.get("retrieval", []):
        for m in ("recall", "hit", "mrr", "ndcg"):
            out[f"{r['mode']}.{m}"] = r[m]
    for m, v in (report.get("agent") or {}).items():
        if isinstance(v, (int, float)):
            out[f"agent.{m}"] = v
    return out


def gate(current: dict[str, float], baseline: dict[str, float]) -> list[str]:
    failures = []
    for key, (direction, tol) in GATES.items():
        b, c = baseline.get(key), current.get(key)
        if b is None or c is None or (isinstance(b, float) and math.isnan(b)) or \
                (isinstance(c, float) and math.isnan(c)):
            continue
        if direction == "higher" and c < b - tol:
            failures.append(f"{key}: {c:.3f} < baseline {b:.3f} - {tol}")
    return failures


def run(engagement_id: str, modes: list[str], k: int, agent: bool = False, judge: bool = False) -> dict:
    golden = load_golden()
    report = {"created_at": datetime.now(timezone.utc), "engagement_id": engagement_id, "k": k,
              "golden_size": len(golden),
              "config": {"llm": settings.llm_provider, "embedding": settings.embedding_provider,
                         "rrf_k": settings.rrf_k, "weights": [settings.vector_weight, settings.text_weight],
                         "chunk_tokens": settings.chunk_tokens},
              "retrieval": [eval_retrieval(engagement_id, m, k, golden) for m in modes]}
    if agent:
        report["agent"] = eval_agent(engagement_id, golden, judge)
    report["summary"] = flatten(report)
    db.eval_runs().insert_one(dict(report))
    report.pop("_id", None)
    return report


def _print(report: dict, verbose: bool) -> None:
    k = report["k"]
    print(f"\nRetrieval - {report['golden_size']} golden questions, k={k}, config={report['config']}")
    print(f"{'mode':8} {'recall@k':>9} {'hit@k':>7} {'MRR':>6} {'nDCG@k':>7} {'p50 ms':>8} {'p95 ms':>8}  backend")
    for r in report["retrieval"]:
        print(f"{r['mode']:8} {r['recall']:9.3f} {r['hit']:7.3f} {r['mrr']:6.3f} {r['ndcg']:7.3f} "
              f"{r['p50_ms']:8.1f} {r['p95_ms']:8.1f}  {r['backend']}")
        if verbose:
            for q in r["per_question"]:
                print(f"   {q['id']} recall={q['recall']:.2f} mrr={q['mrr']:.2f} {q['retrieved']}")
    a = report.get("agent")
    if a:
        print(f"\nAgent: abstention_acc={a['abstention_accuracy']:.2f} citation_precision={a['citation_precision']:.2f} "
              f"faithfulness={a['faithfulness']:.2f} p50={a['p50_latency_ms']:.0f}ms p95={a['p95_latency_ms']:.0f}ms "
              f"mean_cost=${a['mean_cost_usd']:.4f}")
        for q in a["per_question"]:
            print(f"   {q['id']} {q['status']:22} abstention_ok={q['abstention_correct']!s:5} "
                  f"cite_p={q['citation_precision']:.2f} faith={q.get('faithfulness', float('nan')):.2f}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--engagement", default="acme-payments")
    ap.add_argument("--modes", default="vector,text,hybrid")
    ap.add_argument("--k", type=int, default=5)
    ap.add_argument("--agent", action="store_true", help="run the full agent graph (uses the LLM)")
    ap.add_argument("--judge", action="store_true", help="LLM-as-judge faithfulness on answered questions")
    ap.add_argument("--ingest", help="ingest this path first (useful with MONGODB_URI=mongomock://)")
    ap.add_argument("--save-baseline", action="store_true")
    ap.add_argument("--gate", action="store_true", help="exit 1 if metrics regress vs eval/baseline.json")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    if args.ingest:
        from verity.ingest.pipeline import ingest_path
        ingest_path(args.ingest, args.engagement)

    report = run(args.engagement, args.modes.split(","), args.k, args.agent, args.judge)
    _print(report, args.verbose)

    if args.save_baseline:
        BASELINE.write_text(json.dumps({"config": report["config"], "metrics": report["summary"]}, indent=2))
        print(f"\nbaseline written to {BASELINE}")
    if args.gate:
        if not BASELINE.exists():
            sys.exit("no baseline - run with --save-baseline first")
        base = json.loads(BASELINE.read_text())
        if base["config"] != report["config"]:
            print(f"!! config differs from baseline ({base['config']}) - comparison may not be meaningful")
        failures = gate(report["summary"], base["metrics"])
        if failures:
            print("\nEVAL GATE FAILED:\n  " + "\n  ".join(failures))
            sys.exit(1)
        print("\neval gate passed")


if __name__ == "__main__":
    main()
