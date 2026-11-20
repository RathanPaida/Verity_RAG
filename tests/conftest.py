"""Offline test setup: fake LLM, hash embeddings, in-memory MongoDB (mongomock) without search indexes."""
import os

os.environ.update({"LLM_PROVIDER": "fake", "EMBEDDING_PROVIDER": "hash", "EMBEDDING_DIM": "256",
                   "VERITY_API_KEY": "", "CHECKPOINTER": "memory", "MONGODB_URI": "mongomock://"})

import mongomock  # noqa: E402
import pytest  # noqa: E402

from verity import db  # noqa: E402

_NoSearch = db.NoSearchProxy


@pytest.fixture()
def mdb(monkeypatch):
    d = mongomock.MongoClient(tz_aware=True)["verity_test"]
    for name in ("sources", "chunks", "memories", "runs", "eval_runs", "answer_cache"):
        monkeypatch.setattr(db, name, (lambda n=name: _NoSearch(d[n])))
    return d


@pytest.fixture()
def ingested(mdb):
    from verity.ingest.pipeline import ingest_path

    res = ingest_path(os.path.join(os.path.dirname(__file__), "..", "data", "sample"), "acme-payments")
    return mdb, res
