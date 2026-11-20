"""Safe aggregation over structured records (CSV/JSON rows stored as chunks with typed `fields`).

The agent passes a small JSON spec, never raw MQL - field names and operators are whitelisted,
so a prompt-injected document can't turn the tool into arbitrary database access.

spec = {"source": "incidents",                       # which table (source key); optional if only one exists
        "match": {"severity": "SEV1", "date": {"gte": "2024-01-01"}},
        "group_by": "service", "metric": "count" | "sum:minutes_down" | "avg:minutes_down", "limit": 10}
"""
from __future__ import annotations

import re

from .. import db
from ..ingest.loaders import parse_date

_FIELD = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,40}$")
_SOURCE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_OPS = {"eq": "$eq", "ne": "$ne", "gt": "$gt", "gte": "$gte", "lt": "$lt", "lte": "$lte", "in": "$in"}


class SpecError(ValueError):
    pass


def _val(v):
    if isinstance(v, str):
        return parse_date(v) or v
    if isinstance(v, list):
        return [_val(x) for x in v]
    return v


def build_pipeline(engagement_id: str, spec: dict) -> list[dict]:
    match: dict = {"engagement_id": engagement_id, "status": "active", "fields": {"$ne": None}}
    if spec.get("source"):
        if not _SOURCE.match(str(spec["source"])):
            raise SpecError(f"bad source {spec['source']!r}")
        match["source_id"] = {"$regex": f"^{re.escape(engagement_id)}:{re.escape(spec['source'])}@"}
    for fname, cond in (spec.get("match") or {}).items():
        if not _FIELD.match(fname):
            raise SpecError(f"bad field {fname!r}")
        if isinstance(cond, dict):
            m = {}
            for op, v in cond.items():
                if op not in _OPS:
                    raise SpecError(f"bad operator {op!r}")
                m[_OPS[op]] = _val(v)
            match[f"fields.{fname}"] = m
        elif isinstance(cond, list):  # a bare list means "any of" (models often write this)
            match[f"fields.{fname}"] = {"$in": _val(cond)}
        else:
            match[f"fields.{fname}"] = _val(cond)

    pipeline: list[dict] = [{"$match": match}]
    group_by = spec.get("group_by")
    metric = spec.get("metric", "count")
    limit = min(int(spec.get("limit", 10)), 50)
    if group_by:
        if not _FIELD.match(group_by):
            raise SpecError(f"bad group_by {group_by!r}")
        if metric == "count":
            acc = {"$sum": 1}
        else:
            op, _, fname = metric.partition(":")
            if op not in ("sum", "avg", "max", "min") or not _FIELD.match(fname):
                raise SpecError(f"bad metric {metric!r}")
            acc = {f"${op}": f"$fields.{fname}"}
        pipeline += [{"$group": {"_id": f"$fields.{group_by}", "value": acc, "chunk_ids": {"$push": "$_id"}}},
                     {"$sort": {"value": -1}}, {"$limit": limit}]
    else:
        pipeline += [{"$limit": limit}, {"$project": {"embedding": 0}}]
    return pipeline


def query_records(engagement_id: str, spec: dict) -> dict:
    pipeline = build_pipeline(engagement_id, spec)
    rows = list(db.chunks().aggregate(pipeline))
    if spec.get("group_by"):
        result = [{"group": r["_id"], "value": r["value"], "chunk_ids": r["chunk_ids"][:5]} for r in rows]
    else:
        result = [{"chunk_ids": [r["_id"]], "fields": r["fields"]} for r in rows]
    return {"spec": spec, "rows": result}


def record_schema(engagement_id: str, max_values: int = 6) -> str:
    """Per-table field names with types and example values, so the agent writes matching specs."""
    docs = list(db.chunks().find({"engagement_id": engagement_id, "status": "active", "fields": {"$ne": None}},
                                 {"fields": 1, "source_id": 1}).limit(2000))
    if not docs:
        return "none"
    tables: dict[str, list[dict]] = {}
    for d in docs:
        tables.setdefault(d["source_id"].split(":", 1)[-1].split("@")[0], []).append(d["fields"])
    out = []
    for src, rows in sorted(tables.items()):
        parts = []
        for name in rows[0]:
            vals = [r.get(name) for r in rows if r.get(name) is not None]
            sample = vals[0] if vals else None
            if hasattr(sample, "strftime"):
                parts.append(f"{name} (date, e.g. {sample:%Y-%m-%d}; filter with gte/lte 'YYYY-MM-DD')")
            elif isinstance(sample, (int, float)):
                parts.append(f"{name} (number, {min(vals)}..{max(vals)})")
            else:
                distinct = sorted({str(v) for v in vals})
                more = "" if len(distinct) <= max_values else f", ... {len(distinct)} distinct"
                parts.append(f"{name} (text: {', '.join(distinct[:max_values])}{more})")
        out.append(f'source "{src}" ({len(rows)} rows): ' + "; ".join(parts))
    return "\n  ".join(out)
