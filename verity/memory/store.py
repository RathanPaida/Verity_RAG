"""Engagement memory (Track 1, lightweight): analyst preferences, client statements, finding feedback, decisions.

Memories are recalled into the Supervisor/Writer prompts so they visibly change the next brief.
Lifecycle: create -> (supersede via correct) -> soft delete; optional TTL through `expires_at`.
Full lifecycle reference implementation: ../recallops.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from bson import ObjectId
from pymongo.errors import OperationFailure

from .. import db
from ..embeddings import cosine, embed_query, get_embedder
from ..security import redact

TYPES = ["preference", "client_statement", "finding_feedback", "decision", "constraint"]


def remember(engagement_id: str, user_id: str, type: str, content: str, ttl_days: float | None = None,
             supersedes: str | None = None) -> dict:
    if type not in TYPES:
        raise ValueError(f"type must be one of {TYPES}")
    clean, findings = redact(content)
    now = datetime.now(timezone.utc)
    doc = {"engagement_id": engagement_id, "owner_id": user_id, "type": type, "content": clean,
           "embedding": get_embedder().embed([clean])[0], "status": "active", "privacy_findings": findings,
           "created_at": now, "supersedes": ObjectId(supersedes) if supersedes else None}
    if ttl_days:
        doc["expires_at"] = now + timedelta(days=ttl_days)
    doc["_id"] = db.memories().insert_one(doc).inserted_id
    if supersedes:
        db.memories().update_one({"_id": ObjectId(supersedes)},
                                 {"$set": {"status": "superseded", "superseded_by": doc["_id"]}})
    return _public(doc)


def forget(memory_id: str) -> None:
    db.memories().update_one({"_id": ObjectId(memory_id)}, {"$set": {"status": "deleted"}, "$unset": {"embedding": ""}})


def recall(engagement_id: str, query: str, k: int = 5, min_score: float = 0.55) -> list[dict]:
    qv = embed_query(query)
    filt = {"$and": [{"engagement_id": engagement_id}, {"status": "active"}]}
    try:
        docs = list(db.memories().aggregate([
            {"$vectorSearch": {"index": "memories_vector", "path": "embedding", "queryVector": qv,
                               "numCandidates": 100, "limit": k, "filter": filt}},
            {"$addFields": {"score": {"$meta": "vectorSearchScore"}}}]))
    except OperationFailure:
        docs = [{**d, "score": (1 + cosine(qv, d["embedding"])) / 2}
                for d in db.memories().find(filt) if d.get("embedding")]
        docs.sort(key=lambda d: d["score"], reverse=True)
    now = datetime.now(timezone.utc)
    out = []
    for d in docs[:k]:
        exp = d.get("expires_at")
        if exp and (exp if exp.tzinfo else exp.replace(tzinfo=timezone.utc)) < now:
            continue
        # constraints & preferences always apply; others need semantic relevance
        if d["type"] in ("constraint", "preference") or d["score"] >= min_score:
            out.append(_public(d))
    # standing preferences/constraints even when not similar
    seen = {m["id"] for m in out}
    for d in db.memories().find({"engagement_id": engagement_id, "status": "active",
                                 "type": {"$in": ["preference", "constraint"]}}).limit(5):
        if str(d["_id"]) not in seen:
            out.append(_public(d))
    return out


def _public(d: dict) -> dict:
    return {"id": str(d["_id"]), "type": d["type"], "content": d["content"], "owner_id": d.get("owner_id"),
            "score": round(d.get("score", 0), 3), "created_at": d.get("created_at")}
