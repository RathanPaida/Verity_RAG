"""Hybrid retrieval: Atlas $vectorSearch + Atlas Search ($search, BM25) fused with Reciprocal Rank Fusion.

Both legs share the same metadata filters (engagement, kind, owner, date range, source ids).
If Atlas search indexes are unavailable (local dev / tests) each leg falls back to a Python
implementation (brute-force cosine, BM25) so the rest of the system keeps working.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime

from pymongo.errors import OperationFailure

from .. import db
from ..config import settings
from ..embeddings import cosine, get_embedder

_backend = {"vector": "unknown", "text": "unknown"}


@dataclass
class Filters:
    engagement_id: str
    kinds: list[str] | None = None
    owners: list[str] | None = None
    source_ids: list[str] | None = None
    date_from: datetime | None = None
    date_to: datetime | None = None

    def mql(self) -> dict:
        """Filter for $vectorSearch and for Python fallbacks (MQL)."""
        c: list[dict] = [{"engagement_id": self.engagement_id}, {"status": "active"}]
        if self.kinds:
            c.append({"kind": {"$in": self.kinds}})
        if self.owners:
            c.append({"owner": {"$in": self.owners}})
        if self.source_ids:
            c.append({"source_id": {"$in": self.source_ids}})
        if self.date_from or self.date_to:
            rng = {}
            if self.date_from:
                rng["$gte"] = self.date_from
            if self.date_to:
                rng["$lte"] = self.date_to
            c.append({"doc_date": rng})
        return {"$and": c}

    def search_clauses(self) -> list[dict]:
        """Same filter expressed as Atlas Search compound.filter clauses."""
        c: list[dict] = [{"equals": {"path": "engagement_id", "value": self.engagement_id}},
                         {"equals": {"path": "status", "value": "active"}}]
        for path, vals in (("kind", self.kinds), ("owner", self.owners), ("source_id", self.source_ids)):
            if vals:
                c.append({"in": {"path": path, "value": vals}})
        if self.date_from or self.date_to:
            rng: dict = {"path": "doc_date"}
            if self.date_from:
                rng["gte"] = self.date_from
            if self.date_to:
                rng["lte"] = self.date_to
            c.append({"range": rng})
        return c


@dataclass
class Hit:
    chunk_id: str
    source_id: str
    title: str
    kind: str
    section_path: list[str]
    text: str
    doc_date: datetime | None
    fields: dict | None
    score: float = 0.0
    vector_rank: int | None = None
    text_rank: int | None = None
    vector_score: float | None = None
    text_score: float | None = None
    extra: dict = field(default_factory=dict)

    @classmethod
    def from_doc(cls, d: dict) -> "Hit":
        return cls(chunk_id=d["_id"], source_id=d["source_id"], title=d.get("title", ""), kind=d.get("kind", ""),
                   section_path=d.get("section_path", []), text=d["text"], doc_date=d.get("doc_date"),
                   fields=d.get("fields"))

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items()}


_PROJECT = {"embedding": 0}


def vector_search(query: str, f: Filters, k: int) -> list[tuple[dict, float]]:
    qv = get_embedder().embed([query], kind="query")[0]
    pipeline = [
        {"$vectorSearch": {"index": settings.vector_index, "path": "embedding", "queryVector": qv,
                           "numCandidates": max(100, k * 15), "limit": k, "filter": f.mql()}},
        {"$project": {**_PROJECT, "_score": {"$meta": "vectorSearchScore"}}},
    ]
    try:
        docs = list(db.chunks().aggregate(pipeline))
        _backend["vector"] = "atlas"
        return [(d, d.pop("_score")) for d in docs]
    except OperationFailure:
        _backend["vector"] = "python-cosine"
        scored = [(d, (1 + cosine(qv, d["embedding"])) / 2)  # same (1+cos)/2 scale as Atlas
                  for d in db.chunks().find(f.mql()) if d.get("embedding")]
        scored.sort(key=lambda x: x[1], reverse=True)
        out = []
        for d, s in scored[:k]:
            d.pop("embedding", None)
            out.append((d, s))
        return out


_TOKEN = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
_STOP = set("the a an and or of to in on for is are was were be by with what which how why when who does do "
            "did has have this that it its at as from".split())


def tokenize(text: str) -> list[str]:
    return [t for t in _TOKEN.findall(text.lower()) if t not in _STOP]


def stem(tok: str) -> str:
    for suf in ("ings", "ing", "ies", "ed", "es", "s"):
        if len(tok) > len(suf) + 3 and tok.endswith(suf):
            return tok[: -len(suf)] + ("y" if suf == "ies" else "")
    return tok


def stems(text: str) -> set[str]:
    return {stem(t) for t in tokenize(text)}


_idf_cache: dict[tuple[str, int], dict[str, float]] = {}


def corpus_idf(engagement_id: str) -> dict[str, float]:
    """IDF over the engagement's active chunks (cached per chunk count). Unseen terms get the max IDF."""
    n = db.chunks().count_documents({"engagement_id": engagement_id, "status": "active"})
    key = (engagement_id, n)
    if key not in _idf_cache:
        df: Counter = Counter()
        for d in db.chunks().find({"engagement_id": engagement_id, "status": "active"}, {"text": 1, "title": 1}):
            df.update(stems(f"{d.get('title', '')} {d['text']}"))
        _idf_cache.clear()
        _idf_cache[key] = {t: math.log(1 + (n + 1) / (c + 0.5)) for t, c in df.items()}
        _idf_cache[key]["__max__"] = math.log(1 + (n + 1) / 0.5)
    return _idf_cache[key]


def informative_coverage(query: str, text: str, idf: dict[str, float]) -> float:
    """Share of the query's information (IDF mass) present in text. Rare query terms that the corpus
    never mentions (e.g. 'SOC', 'bill') pull coverage down - a cheap answerability signal."""
    q = stems(query)
    if not q:
        return 0.0
    mx = idf.get("__max__", 5.0)
    total = sum(idf.get(t, mx) for t in q)
    present = stems(text)
    return sum(idf.get(t, mx) for t in q if t in present) / total


def bm25(query: str, docs: list[dict], k1: float = 1.5, b: float = 0.75) -> list[tuple[dict, float]]:
    q = tokenize(query)
    toks = [tokenize(f"{d.get('title', '')} {d['text']}") for d in docs]
    if not docs or not q:
        return []
    avgdl = sum(map(len, toks)) / len(toks)
    df = Counter(t for ts in toks for t in set(ts))
    n = len(docs)
    out = []
    for d, ts in zip(docs, toks):
        tf = Counter(ts)
        s = 0.0
        for t in q:
            if tf[t]:
                idf = math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5))
                s += idf * tf[t] * (k1 + 1) / (tf[t] + k1 * (1 - b + b * len(ts) / avgdl))
        if s > 0:
            out.append((d, s))
    return sorted(out, key=lambda x: x[1], reverse=True)


def text_search(query: str, f: Filters, k: int) -> list[tuple[dict, float]]:
    pipeline = [
        {"$search": {"index": settings.text_index, "compound": {
            "must": [{"text": {"query": query, "path": ["text", "title", "section_path"]}}],
            "filter": f.search_clauses()}}},
        {"$limit": k},
        {"$project": {**_PROJECT, "_score": {"$meta": "searchScore"}}},
    ]
    try:
        docs = list(db.chunks().aggregate(pipeline))
        _backend["text"] = "atlas"
        return [(d, d.pop("_score")) for d in docs]
    except OperationFailure:
        _backend["text"] = "python-bm25"
        docs = list(db.chunks().find(f.mql(), _PROJECT))
        return bm25(query, docs)[:k]


def rrf_fuse(ranked_lists: list[tuple[list[str], float]], k: int = 60) -> dict[str, float]:
    """Reciprocal Rank Fusion. ranked_lists = [(ids_in_rank_order, weight)]."""
    scores: dict[str, float] = {}
    for ids, w in ranked_lists:
        for rank, cid in enumerate(ids, 1):
            scores[cid] = scores.get(cid, 0.0) + w / (k + rank)
    return scores


def hybrid_search(query: str, f: Filters, k: int | None = None, mode: str = "hybrid") -> list[Hit]:
    k = k or settings.top_k
    pool = k * 3
    vec = vector_search(query, f, pool) if mode in ("vector", "hybrid") else []
    txt = text_search(query, f, pool) if mode in ("text", "hybrid") else []

    docs: dict[str, dict] = {}
    for d, _ in vec + txt:
        docs.setdefault(d["_id"], d)
    fused = rrf_fuse([([d["_id"] for d, _ in vec], settings.vector_weight),
                      ([d["_id"] for d, _ in txt], settings.text_weight)], settings.rrf_k)

    hits = []
    vrank = {d["_id"]: (i, s) for i, (d, s) in enumerate(vec, 1)}
    trank = {d["_id"]: (i, s) for i, (d, s) in enumerate(txt, 1)}
    for cid, score in sorted(fused.items(), key=lambda x: x[1], reverse=True)[:k]:
        h = Hit.from_doc(docs[cid])
        h.score = round(score, 5)
        if cid in vrank:
            h.vector_rank, h.vector_score = vrank[cid][0], round(vrank[cid][1], 4)
        if cid in trank:
            h.text_rank, h.text_score = trank[cid][0], round(trank[cid][1], 4)
        hits.append(h)
    return hits


def expand_context(chunk_id: str, window: int = 1) -> list[Hit]:
    """Neighbouring chunks of the same source (for 'distant section' follow-ups)."""
    c = db.chunks().find_one({"_id": chunk_id}, _PROJECT)
    if not c:
        return []
    near = db.chunks().find({"source_id": c["source_id"], "chunk_index": {
        "$gte": c["chunk_index"] - window, "$lte": c["chunk_index"] + window}}, _PROJECT).sort("chunk_index", 1)
    return [Hit.from_doc(d) for d in near]


def backends() -> dict:
    return dict(_backend)
