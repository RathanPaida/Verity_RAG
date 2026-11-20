"""MongoDB collections and index management (vector + Atlas Search + TTL + regular)."""
from __future__ import annotations

import time
from functools import lru_cache

from pymongo import ASCENDING, MongoClient
from pymongo.errors import OperationFailure
from pymongo.operations import SearchIndexModel

from .config import settings


def is_mock() -> bool:
    """MONGODB_URI=mongomock:// runs the whole app in-memory (demo/CI without Atlas)."""
    return settings.mongodb_uri.startswith("mongomock://")


class NoSearchProxy:
    """Collection proxy for deployments without Atlas Search: $vectorSearch/$search raise OperationFailure,
    which makes the retrieval layer use its Python fallbacks."""

    def __init__(self, coll):
        self._c = coll

    def aggregate(self, pipeline, *a, **k):
        if pipeline and ("$vectorSearch" in pipeline[0] or "$search" in pipeline[0]):
            raise OperationFailure("search indexes are not available on this deployment")
        return self._c.aggregate(pipeline, *a, **k)

    def __getattr__(self, name):
        return getattr(self._c, name)


@lru_cache(maxsize=1)
def client():
    if is_mock():
        import mongomock

        return mongomock.MongoClient(tz_aware=True)
    return MongoClient(settings.mongodb_uri, appname="verity", tz_aware=True)


def _db():
    return client()[settings.mongodb_db]


def _coll(name: str):
    c = _db()[name]
    return NoSearchProxy(c) if is_mock() else c


def sources():
    return _coll("sources")


def chunks():
    return _coll("chunks")


def memories():
    return _coll("memories")


def runs():
    return _coll("runs")


def eval_runs():
    return _coll("eval_runs")


def answer_cache():
    return _coll("answer_cache")


FILTER_FIELDS = ["engagement_id", "kind", "owner", "doc_date", "source_id", "status"]


def vector_index_def(dim: int) -> dict:
    return {"fields": [{"type": "vector", "path": "embedding", "numDimensions": dim, "similarity": "cosine"}]
            + [{"type": "filter", "path": f} for f in FILTER_FIELDS]}


TEXT_INDEX_DEF = {
    "analyzer": "lucene.english",
    "mappings": {
        "dynamic": False,
        "fields": {
            "text": {"type": "string"},
            "title": {"type": "string"},
            "section_path": {"type": "string"},
            "engagement_id": {"type": "token"},
            "kind": {"type": "token"},
            "owner": {"type": "token"},
            "source_id": {"type": "token"},
            "status": {"type": "token"},
            "doc_date": {"type": "date"},
        },
    },
}

MEMORY_VECTOR_DEF = lambda dim: {"fields": [  # noqa: E731
    {"type": "vector", "path": "embedding", "numDimensions": dim, "similarity": "cosine"},
    {"type": "filter", "path": "engagement_id"}, {"type": "filter", "path": "status"},
]}


def _ensure_search_index(coll, name: str, kind: str, definition: dict, log) -> None:
    existing = {i["name"] for i in coll.list_search_indexes()}
    if name in existing:
        coll.update_search_index(name, definition)
        log(f"updated {kind} index {coll.name}.{name}")
    else:
        coll.create_search_index(SearchIndexModel(definition=definition, name=name, type=kind))
        log(f"created {kind} index {coll.name}.{name}")


def ensure_indexes(dim: int, wait: bool = True, log=print) -> None:
    d = _db()
    for name in ("sources", "chunks", "memories", "runs", "eval_runs", "answer_cache"):
        if name not in d.list_collection_names():
            d.create_collection(name)

    sources().create_index([("engagement_id", 1), ("source_key", 1), ("version", 1)], unique=True)
    chunks().create_index([("engagement_id", 1), ("kind", 1), ("fields.service", 1)])
    chunks().create_index([("source_id", 1), ("chunk_index", 1)])
    memories().create_index([("expires_at", ASCENDING)], expireAfterSeconds=0)
    runs().create_index([("created_at", ASCENDING)], expireAfterSeconds=30 * 86400)
    runs().create_index([("engagement_id", 1), ("created_at", -1)])
    answer_cache().create_index([("created_at", ASCENDING)],
                                expireAfterSeconds=max(1, int(settings.cache_ttl_hours * 3600)))
    answer_cache().create_index([("engagement_id", 1), ("fingerprint", 1)])
    log("regular + TTL indexes ok")
    if is_mock():
        log("mongomock: skipping search indexes (Python fallbacks will be used)")
        return

    try:
        _ensure_search_index(chunks(), settings.vector_index, "vectorSearch", vector_index_def(dim), log)
        _ensure_search_index(chunks(), settings.text_index, "search", TEXT_INDEX_DEF, log)
        _ensure_search_index(memories(), "memories_vector", "vectorSearch", MEMORY_VECTOR_DEF(dim), log)
    except OperationFailure as e:
        log(f"!! search indexes unavailable ({e.details.get('errmsg', e) if e.details else e}). "
            "Use Atlas or mongodb/mongodb-atlas-local; Python fallbacks will be used.")
        return

    if wait:
        for _ in range(120):
            states = [i.get("queryable") for c in (chunks(), memories()) for i in c.list_search_indexes()]
            if states and all(states):
                log("all search indexes queryable")
                return
            time.sleep(2)
        log("!! search indexes still building; fallbacks will be used until ready")
