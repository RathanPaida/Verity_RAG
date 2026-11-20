"""Explain a $vectorSearch query (Atlas) to debug retrieval - candidates examined, timings, filter use.

    python -m scripts.explain_query "ledger-db cross region replica" --engagement acme-payments
"""
import argparse
import json

from bson import json_util

from verity import db
from verity.config import settings
from verity.embeddings import get_embedder
from verity.retrieval.hybrid import Filters

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("query")
    ap.add_argument("--engagement", default="acme-payments")
    ap.add_argument("--k", type=int, default=5)
    a = ap.parse_args()
    qv = get_embedder().embed([a.query], kind="query")[0]
    pipeline = [{"$vectorSearch": {"index": settings.vector_index, "path": "embedding", "queryVector": qv,
                                   "numCandidates": 150, "limit": a.k, "filter": Filters(a.engagement).mql()}},
                {"$project": {"embedding": 0}}]
    plan = db.client()[settings.mongodb_db].command(
        "explain", {"aggregate": "chunks", "pipeline": pipeline, "cursor": {}}, verbosity="executionStats")
    print(json.dumps(json.loads(json_util.dumps(plan)), indent=2)[:6000])
