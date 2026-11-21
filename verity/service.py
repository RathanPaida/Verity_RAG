"""Application service: semantic cache -> agent graph inside a traced Run -> persist result."""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone

from . import db
from .agents.graph import graph
from .config import settings
from .embeddings import cosine, embed_query
from .observability.tracing import start_run
from .retrieval.hybrid import backends

_RESULT_KEYS = ("status", "answer", "citations", "conflicts", "discovery", "plan", "verification", "tool_log",
                "retrieval_backends")


def corpus_fingerprint(engagement_id: str) -> str:
    """Changes whenever sources or memories of the engagement change -> invalidates cached answers."""
    src = sorted(d["_id"] for d in db.sources().find({"engagement_id": engagement_id, "status": "active"}, {"_id": 1}))
    mem = sorted(str(d["_id"]) for d in db.memories().find({"engagement_id": engagement_id, "status": "active"},
                                                            {"_id": 1}))
    return hashlib.sha256("|".join(src + ["#"] + mem).encode()).hexdigest()[:16]


def _cache_lookup(engagement_id: str, qvec: list[float], fp: str, filters: dict | None) -> dict | None:
    if settings.cache_ttl_hours <= 0:
        return None
    since = datetime.now(timezone.utc) - timedelta(hours=settings.cache_ttl_hours)
    best, best_sim = None, 0.0
    for d in db.answer_cache().find({"engagement_id": engagement_id, "fingerprint": fp,
                                     "filters": filters or {}, "created_at": {"$gte": since}}).limit(500):
        sim = cosine(qvec, d["embedding"])
        if sim > best_sim:
            best, best_sim = d, sim
    if best and best_sim >= settings.cache_min_similarity:
        return {**best["result"], "cache": {"hit": True, "similarity": round(best_sim, 4),
                                            "cached_question": best["question"],
                                            "original_run_id": best["result"]["run_id"]}}
    return None


def ask(question: str, engagement_id: str, user_id: str = "analyst", filters: dict | None = None,
        use_cache: bool = True) -> dict:
    fp = corpus_fingerprint(engagement_id)
    qvec = embed_query(question) if use_cache else None
    if use_cache:
        assert qvec is not None
        hit = _cache_lookup(engagement_id, qvec, fp, filters)
        if hit:
            hit["metrics"] = {**hit["metrics"], "latency_ms": 0.0, "cost_usd": 0.0, "llm_calls": 0}
            return hit

    with start_run(question, engagement_id) as run:
        state = graph().invoke(
            {"run_id": run.run_id, "question": question, "engagement_id": engagement_id, "user_id": user_id,
             "filters": filters or {}},
            config={"configurable": {"thread_id": run.run_id}, "recursion_limit": 25},
        )
        doc = run.to_doc()
    result = {
        "run_id": run.run_id,
        "status": state.get("status"),
        "answer": state.get("answer"),
        "citations": state.get("citations", []),
        "conflicts": state.get("conflicts", []),
        "discovery": state.get("discovery", []),
        "plan": state.get("plan", []),
        "memories_used": state.get("memories", []),
        "verification": state.get("verification", {}),
        "tool_log": state.get("tool_log", []),
        "evidence_count": len(state.get("evidence") or {}),
        "retrieval_backends": backends(),
        "metrics": doc["totals"],
        "spans": doc["spans"],
        "cache": {"hit": False},
    }
    doc.update({k: result[k] for k in _RESULT_KEYS})
    doc["user_id"] = user_id
    db.runs().replace_one({"_id": run.run_id}, doc, upsert=True)
    if use_cache and result["status"] == "answered":
        db.answer_cache().insert_one({"engagement_id": engagement_id, "question": question, "embedding": qvec,
                                      "fingerprint": fp, "filters": filters or {}, "result": result,
                                      "created_at": datetime.now(timezone.utc)})
    return result
