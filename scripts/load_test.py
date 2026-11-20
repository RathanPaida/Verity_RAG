"""Concurrent load test against a running API: throughput, latency percentiles, cost/request, tokens/sec.

    python -m scripts.load_test --url http://localhost:8000 --concurrency 4 --requests 20 [--no-cache]
"""
import argparse
import json
import random
import statistics
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from eval.run_eval import load_golden


def call(url, key, engagement, question, use_cache):
    req = urllib.request.Request(f"{url}/ask", method="POST", data=json.dumps(
        {"engagement_id": engagement, "question": question, "use_cache": use_cache}).encode(),
        headers={"Content-Type": "application/json", "X-API-Key": key})
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=600) as r:
            body = json.loads(r.read())
        return {"ok": True, "wall_ms": (time.perf_counter() - t0) * 1000, **body["metrics"],
                "cache_hit": body["cache"]["hit"], "status": body["status"]}
    except Exception as e:  # noqa: BLE001 - report every failure
        return {"ok": False, "wall_ms": (time.perf_counter() - t0) * 1000, "error": str(e)}


def pct(xs, p):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(p / 100 * len(xs)))] if xs else float("nan")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8000")
    ap.add_argument("--key", default="")
    ap.add_argument("--engagement", default="acme-payments")
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--requests", type=int, default=20)
    ap.add_argument("--no-cache", action="store_true")
    a = ap.parse_args()
    qs = [g["question"] for g in load_golden()]
    t0 = time.perf_counter()
    with ThreadPoolExecutor(a.concurrency) as pool:
        res = list(pool.map(lambda _: call(a.url, a.key, a.engagement, random.choice(qs), not a.no_cache),
                            range(a.requests)))
    wall = time.perf_counter() - t0
    ok = [r for r in res if r["ok"]]
    lat = [r["wall_ms"] for r in ok]
    print(f"requests={len(res)} ok={len(ok)} errors={len(res) - len(ok)} concurrency={a.concurrency}")
    print(f"throughput={len(ok) / wall:.2f} req/s  wall p50={pct(lat, 50):.0f}ms p95={pct(lat, 95):.0f}ms "
          f"max={max(lat, default=0):.0f}ms")
    if ok:
        print(f"cost/request=${statistics.mean(r['cost_usd'] for r in ok):.5f}  "
              f"avg output tok/s={statistics.mean(r['output_tokens_per_sec'] for r in ok):.1f}  "
              f"cache hit rate={sum(r['cache_hit'] for r in ok) / len(ok):.0%}")
    for r in res:
        if not r["ok"]:
            print("  error:", r["error"])
