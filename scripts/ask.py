"""Ask a question from the terminal and print the brief, citations and run metrics.

    python -m scripts.ask "What are the top reliability risks?" --engagement acme-payments
"""
import argparse
import json

from verity.service import ask

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("question")
    ap.add_argument("--engagement", default="acme-payments")
    ap.add_argument("--trace", action="store_true", help="print per-node spans")
    a = ap.parse_args()
    r = ask(a.question, a.engagement)
    print(f"\n=== {r['status']} (run {r['run_id']}) ===\n\n{r['answer']}\n")
    for c in r["citations"]:
        path = " > ".join(c["section_path"] or [])
        print(f"[{c['label']}] {c['source_id']} :: {path}\n      {c['passage'][:220]!r}")
    if r["conflicts"]:
        print("\nConflicts:", json.dumps(r["conflicts"], indent=1))
    print("\nPlan:", [(p["id"], p["specialist"], p["question"]) for p in r["plan"]])
    print("Verification:", r["verification"].get("route"), "| unsupported:", r["verification"].get("unsupported"))
    print("Metrics:", r["metrics"], "| retrieval:", r["retrieval_backends"])
    if a.trace:
        for s in r["spans"]:
            print(f"  {s['name']:22} {s['ms']:8.1f} ms  in={s['tokens_in']:6} out={s['tokens_out']:6} "
                  f"${s['cost_usd']:.5f} tools={len(s['tool_calls'])}")
