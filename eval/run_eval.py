"""Evaluate retrieval (and optionally the full agent) against the golden set.

    python -m eval.run_eval --modes vector,text,hybrid --k 5            # retrieval comparison
    python -m eval.run_eval --agent --judge                              # + answer quality, safety, cost
    python -m eval.run_eval --agent --judge --resume                     # continue an interrupted agent run
    python -m eval.run_eval --agent --save-baseline                      # write eval/baseline.json
    python -m eval.run_eval --agent --gate                               # CI: exit 1 on regression vs baseline
    python -m eval.run_eval --ingest data/sample                         # ingest first (e.g. mongomock:// in CI)

Golden set: eval/golden.jsonl - categorised questions (keyword, semantic, multi_source, structured, conflict,
temporal, unanswerable, cross_tenant, injection) with expected source IDs, required facts and forbidden strings.
Every headline metric is reported with a 95% bootstrap confidence interval and broken down per category.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from verity import db
from verity.config import settings
from verity.retrieval.hybrid import Filters, backends, hybrid_search

from .metrics import (
    bootstrap_ci,
    citation_precision,
    fact_recall,
    hit_at_k,
    mean,
    mrr,
    ndcg_at_k,
    percentile,
    recall_at_k,
    source_key,
    violates,
)

HERE = Path(__file__).parent
ROOT = HERE.parent
GOLDEN = HERE / "golden.jsonl"
BASELINE = HERE / "baseline.json"
RESULTS = HERE / "results"
OTHER_TENANT = ("data/sample-globex", "globex-logistics")  # second engagement used for leakage tests

# metric -> (direction, tolerated regression)
GATES = {
    "hybrid.recall": ("higher", 0.03),
    "hybrid.mrr": ("higher", 0.05),
    "agent.abstention_accuracy": ("higher", 0.0),
    "agent.citation_precision": ("higher", 0.05),
    "agent.faithfulness": ("higher", 0.05),
    "agent.fact_recall": ("higher", 0.05),
    "agent.safety_pass_rate": ("higher", 0.0),
}


def load_golden(path: Path = GOLDEN) -> list[dict]:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    for r in rows:  # backwards compatible with the v1 schema
        r.setdefault("category", "general")
        r.setdefault("must_include", [])
        r.setdefault("must_not_include", [])
        r.setdefault("expect_conflict", False)
    return rows


def _with_ci(values: list[float]) -> dict:
    lo, hi = bootstrap_ci(values)
    return {"mean": mean(values), "ci95": [lo, hi], "n": sum(1 for v in values if not math.isnan(v))}


def _by_category(rows: list[dict], golden: list[dict], metrics: list[str]) -> dict:
    cat = {g["id"]: g["category"] for g in golden}
    groups: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        groups[cat[r["id"]]].append(r)
    return {c: {"n": len(rs), **{m: mean([float(r[m]) if not isinstance(r.get(m), bool) else float(r[m])
                                          for r in rs if r.get(m) is not None]) for m in metrics}}
            for c, rs in sorted(groups.items())}


def eval_retrieval(engagement_id: str, mode: str, k: int, golden: list[dict]) -> dict:
    allowed = {s["source_key"] for s in db.sources().find({"engagement_id": engagement_id}, {"source_key": 1})}
    rows, lat, leaked = [], [], 0
    for g in golden:
        if not g["expected_sources"]:
            continue
        t0 = time.perf_counter()
        hits = hybrid_search(g["question"], Filters(engagement_id), k=k * 2, mode=mode)
        lat.append((time.perf_counter() - t0) * 1000)
        got = [source_key(h.source_id) for h in hits]
        leaked += sum(1 for s in got if s not in allowed)
        rows.append({"id": g["id"], "recall": recall_at_k(got, g["expected_sources"], k),
                     "hit": hit_at_k(got, g["expected_sources"], k), "mrr": mrr(got, g["expected_sources"]),
                     "ndcg": ndcg_at_k(got, g["expected_sources"], k), "retrieved": got[:k]})
    return {"mode": mode, "k": k, "backend": backends(), "n": len(rows),
            "recall": mean([r["recall"] for r in rows]), "hit": mean([r["hit"] for r in rows]),
            "mrr": mean([r["mrr"] for r in rows]), "ndcg": mean([r["ndcg"] for r in rows]),
            "recall_ci95": bootstrap_ci([r["recall"] for r in rows]),
            "mrr_ci95": bootstrap_ci([r["mrr"] for r in rows]),
            "cross_tenant_leaks": leaked,
            "by_category": _by_category(rows, golden, ["recall", "mrr"]),
            "p50_ms": percentile(lat, 50), "p95_ms": percentile(lat, 95), "per_question": rows}


def _config_key() -> str:
    cfg = f"{settings.llm_provider}|{settings.model_fast}|{settings.model_strong}|{settings.embedding_provider}"
    return hashlib.sha1(cfg.encode()).hexdigest()[:8]


def _score(g: dict, r: dict, judge: bool) -> dict:
    from .judge import faithfulness

    cited = [source_key(c["source_id"]) for c in r["citations"]]
    answered = r["status"] == "answered"
    row = {"id": g["id"], "category": g["category"], "status": r["status"],
           "abstention_correct": (not answered) == (not g["answerable"]),
           "citation_precision": citation_precision(cited, g["expected_sources"]) if g["answerable"] and answered
           else float("nan"),
           "fact_recall": fact_recall(r["answer"], g["must_include"]) if g["answerable"] else float("nan"),
           "safe": not violates(r["answer"], g["must_not_include"]),
           "conflict_detected": bool(r["conflicts"]) if g["expect_conflict"] else None,
           "latency_ms": r["metrics"]["latency_ms"], "cost_usd": r["metrics"]["cost_usd"],
           "models": r["metrics"].get("models", []), "run_id": r["run_id"]}
    if judge and answered:
        f = faithfulness(r["answer"], r["citations"])
        row["faithfulness"], row["unsupported"] = f["score"], f["unsupported"]
    return row


def eval_agent(engagement_id: str, golden: list[dict], judge: bool = False, resume: bool = False) -> dict:
    from verity.service import ask

    RESULTS.mkdir(exist_ok=True)
    ckpt = RESULTS / f"agent-checkpoint-{engagement_id}-{_config_key()}.jsonl"
    done: dict[str, dict] = {}
    if resume and ckpt.exists():
        for line in ckpt.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            if row["status"] != "error":
                done[row["id"]] = row
    elif ckpt.exists():
        ckpt.unlink()

    rows = []
    for i, g in enumerate(golden, 1):
        if g["id"] in done:
            rows.append(done[g["id"]])
            continue
        try:
            row = _score(g, ask(g["question"], engagement_id, use_cache=False), judge)
        except Exception as e:  # one failed question must not abort the whole evaluation
            row = {"id": g["id"], "category": g["category"], "status": "error", "error": str(e)[:300],
                   "abstention_correct": False, "citation_precision": float("nan"), "fact_recall": float("nan"),
                   "safe": True, "conflict_detected": None, "latency_ms": float("nan"), "cost_usd": 0.0,
                   "models": [], "run_id": None}
        rows.append(row)
        with ckpt.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, default=str) + "\n")
        print(f"   [{i}/{len(golden)}] {g['id']} {row['status']}", flush=True)

    ok = [r for r in rows if r["status"] != "error"]
    conflict_rows = [r for r in ok if r["conflict_detected"] is not None]
    guarded = [r for r in ok if next(g for g in golden if g["id"] == r["id"])["must_not_include"]]
    return {
        "n": len(rows),
        "abstention_accuracy": mean([float(r["abstention_correct"]) for r in ok]),
        "abstention_ci95": bootstrap_ci([float(r["abstention_correct"]) for r in ok]),
        "citation_precision": mean([r["citation_precision"] for r in ok]),
        "citation_precision_ci95": bootstrap_ci([r["citation_precision"] for r in ok]),
        "fact_recall": mean([r["fact_recall"] for r in ok]),
        "fact_recall_ci95": bootstrap_ci([r["fact_recall"] for r in ok]),
        "faithfulness": mean([r.get("faithfulness", float("nan")) for r in ok]),
        "faithfulness_ci95": bootstrap_ci([r.get("faithfulness", float("nan")) for r in ok]),
        "conflict_detection_rate": mean([float(r["conflict_detected"]) for r in conflict_rows]),
        "safety_pass_rate": mean([float(r["safe"]) for r in guarded]),
        "safety_checks": len(guarded),
        "by_category": _by_category(ok, golden, ["abstention_correct", "citation_precision", "fact_recall"]),
        "p50_latency_ms": percentile([r["latency_ms"] for r in ok], 50),
        "p95_latency_ms": percentile([r["latency_ms"] for r in ok], 95),
        "errors": len(rows) - len(ok),
        "mean_cost_usd": mean([r["cost_usd"] for r in ok]),
        "total_cost_usd": sum(r["cost_usd"] for r in ok),
        "models_used": sorted({m for r in ok for m in r.get("models", [])}),
        "per_question": rows,
    }


def flatten(report: dict) -> dict[str, float]:
    out = {}
    for r in report.get("retrieval", []):
        for m in ("recall", "hit", "mrr", "ndcg"):
            out[f"{r['mode']}.{m}"] = r[m]
    for m, v in (report.get("agent") or {}).items():
        if isinstance(v, (int, float)) and not isinstance(v, bool):
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


def ingest_corpora(engagement_id: str, path: str) -> None:
    from verity.ingest.pipeline import ingest_path

    ingest_path(path, engagement_id)
    other_path, other_eng = OTHER_TENANT
    if (ROOT / other_path).exists():
        ingest_path(ROOT / other_path, other_eng)


def run(engagement_id: str, modes: list[str], k: int, agent: bool = False, judge: bool = False,
        resume: bool = False) -> dict:
    golden = load_golden()
    report = {"created_at": datetime.now(timezone.utc), "engagement_id": engagement_id, "k": k,
              "golden_size": len(golden),
              "config": {"llm": settings.llm_provider, "embedding": settings.embedding_provider,
                         "rrf_k": settings.rrf_k, "weights": [settings.vector_weight, settings.text_weight],
                         "chunk_tokens": settings.chunk_tokens},
              "retrieval": [eval_retrieval(engagement_id, m, k, golden) for m in modes]}
    if agent:
        report["agent"] = eval_agent(engagement_id, golden, judge, resume)
    report["summary"] = flatten(report)
    db.eval_runs().insert_one(dict(report))
    report.pop("_id", None)
    return report


def _ci(ci) -> str:
    lo, hi = ci
    return "" if lo != lo else f" [{lo:.2f}-{hi:.2f}]"


def _print(report: dict, verbose: bool) -> None:
    k = report["k"]
    print(f"\nRetrieval - {report['golden_size']} golden questions, k={k}, config={report['config']}")
    print(f"{'mode':8} {'n':>3} {'recall@k [95% CI]':>22} {'MRR [95% CI]':>20} {'nDCG@k':>7} {'leaks':>5}  backend")
    for r in report["retrieval"]:
        print(f"{r['mode']:8} {r['n']:3} {r['recall']:8.3f}{_ci(r['recall_ci95']):>14} {r['mrr']:7.3f}"
              f"{_ci(r['mrr_ci95']):>13} {r['ndcg']:7.3f} {r['cross_tenant_leaks']:5}  {r['backend']}")
    hyb = next((r for r in report["retrieval"] if r["mode"] == "hybrid"), report["retrieval"][-1])
    print(f"  per category ({hyb['mode']}): " + ", ".join(
        f"{c} r={v['recall']:.2f}/n={v['n']}" for c, v in hyb["by_category"].items()))
    if verbose:
        for q in hyb["per_question"]:
            print(f"   {q['id']} recall={q['recall']:.2f} mrr={q['mrr']:.2f} {q['retrieved']}")
    a = report.get("agent")
    if a:
        print(f"\nAgent ({a['n']} questions, errors={a['errors']}, models={a['models_used']}):")
        print(f"  abstention accuracy {a['abstention_accuracy']:.2f}{_ci(a['abstention_ci95'])} | "
              f"citation precision {a['citation_precision']:.2f}{_ci(a['citation_precision_ci95'])} | "
              f"fact recall {a['fact_recall']:.2f}{_ci(a['fact_recall_ci95'])} | "
              f"faithfulness {a['faithfulness']:.2f}{_ci(a['faithfulness_ci95'])}")
        print(f"  conflict detection {a['conflict_detection_rate']:.2f} | safety pass {a['safety_pass_rate']:.2f} "
              f"({a['safety_checks']} checks) | p50 {a['p50_latency_ms']:.0f} ms p95 {a['p95_latency_ms']:.0f} ms | "
              f"mean ${a['mean_cost_usd']:.4f} total ${a['total_cost_usd']:.3f}")
        for c, v in a["by_category"].items():
            print(f"  {c:13} n={v['n']:2} abstention={v['abstention_correct']:.2f} "
                  f"cite_p={v['citation_precision']:.2f} facts={v['fact_recall']:.2f}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--engagement", default="acme-payments")
    ap.add_argument("--modes", default="vector,text,hybrid")
    ap.add_argument("--k", type=int, default=5)
    ap.add_argument("--agent", action="store_true", help="run the full agent graph (uses the LLM)")
    ap.add_argument("--judge", action="store_true", help="LLM-as-judge faithfulness on answered questions")
    ap.add_argument("--resume", action="store_true", help="skip questions already scored with this config")
    ap.add_argument("--ingest", help="ingest this path first (plus the second tenant used for leakage tests)")
    ap.add_argument("--save-baseline", action="store_true")
    ap.add_argument("--gate", action="store_true", help="exit 1 if metrics regress vs eval/baseline.json")
    ap.add_argument("--out", help="also write the full report JSON here")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    if args.ingest:
        ingest_corpora(args.engagement, args.ingest)

    report = run(args.engagement, args.modes.split(","), args.k, args.agent, args.judge, args.resume)
    _print(report, args.verbose)
    if args.out:
        Path(args.out).write_text(json.dumps(report, default=str, indent=1), encoding="utf-8")

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
