"""Verity REST API + web UI (FastAPI). Deployed on Cloud Run; API key from Secret Manager via env."""
from __future__ import annotations

import hmac
import logging
import math
import shutil
import tempfile
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

from bson import ObjectId
from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .. import db, service
from ..config import settings
from ..ingest.gcs import engagement_from_object
from ..ingest.loaders import SUPPORTED
from ..ingest.pipeline import ingest_path
from ..llm.gateway import LLMError
from ..memory import store as memory
from ..observability.tracing import percentile
from ..retrieval.hybrid import Filters, backends, hybrid_search

log = logging.getLogger("verity.api")
logging.basicConfig(level=logging.INFO)
STATIC = Path(__file__).parent / "static"
SAMPLE = Path(__file__).resolve().parents[2] / "data" / "sample"
MAX_UPLOAD_BYTES = 20 * 1024 * 1024
ENGAGEMENT_RE = r"^[a-z0-9-]{3,64}$"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    if db.is_mock():
        db.ensure_indexes(settings.embedding_dim, wait=False, log=log.info)
    if settings.auto_ingest_sample and SAMPLE.exists() and \
            db.sources().count_documents({"engagement_id": settings.demo_engagement}) == 0:
        log.info("AUTO_INGEST_SAMPLE: ingesting %s", SAMPLE)
        ingest_path(SAMPLE, settings.demo_engagement)
    yield


app = FastAPI(title="Verity", version="1.0.0", description="Evidence-grounded due-diligence agents",
              lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


def clean(o):
    """JSON-safe: NaN/inf -> None, ObjectId -> str (Starlette refuses NaN)."""
    if isinstance(o, float) and (math.isnan(o) or math.isinf(o)):
        return None
    if isinstance(o, ObjectId):
        return str(o)
    if isinstance(o, dict):
        return {k: clean(v) for k, v in o.items() if k != "embedding"}
    if isinstance(o, (list, tuple)):
        return [clean(v) for v in o]
    return o


def require_key(x_api_key: str = Header(default="")):
    if settings.api_key and not hmac.compare_digest(x_api_key, settings.api_key):
        raise HTTPException(401, "invalid API key")


Auth = Depends(require_key)


class FilterIn(BaseModel):
    kinds: list[str] | None = None
    owners: list[str] | None = None
    source_ids: list[str] | None = None
    date_from: datetime | None = None
    date_to: datetime | None = None


class AskIn(BaseModel):
    engagement_id: str = Field(pattern=ENGAGEMENT_RE)
    question: str = Field(min_length=5, max_length=2000)
    user_id: str = "analyst"
    filters: FilterIn | None = None
    use_cache: bool = True


class SearchIn(BaseModel):
    engagement_id: str = Field(pattern=ENGAGEMENT_RE)
    query: str = Field(min_length=1, max_length=500)
    mode: Literal["vector", "text", "hybrid"] = "hybrid"
    k: int = Field(8, ge=1, le=50)
    filters: FilterIn | None = None


class FeedbackIn(BaseModel):
    engagement_id: str = Field(pattern=ENGAGEMENT_RE)
    user_id: str = "analyst"
    type: Literal["preference", "client_statement", "finding_feedback", "decision", "constraint"]
    content: str = Field(min_length=3, max_length=2000)
    ttl_days: float | None = Field(None, gt=0)
    supersedes: str | None = None


class EvalIn(BaseModel):
    engagement_id: str = Field(pattern=ENGAGEMENT_RE)
    modes: list[Literal["vector", "text", "hybrid"]] = ["vector", "text", "hybrid"]
    k: int = Field(5, ge=1, le=20)
    agent: bool = False
    judge: bool = False


# ----------------------------------------------------------------------------- UI & health

@app.get("/", include_in_schema=False)
def ui():
    return FileResponse(STATIC / "index.html")


@app.get("/health")
def health():
    return {"ok": True, "llm": settings.llm_provider, "embeddings": settings.embedding_provider,
            "mongo": "mongomock" if db.is_mock() else "atlas", "retrieval_backends": backends(),
            "auth": bool(settings.api_key), "demo_engagement": settings.demo_engagement}


# ----------------------------------------------------------------------------- ingestion

@app.post("/ingest", dependencies=[Auth])
async def ingest(engagement_id: str = Form(..., pattern=ENGAGEMENT_RE), files: list[UploadFile] = File(...)):
    """Upload files (md/txt/html/pdf/csv/json). Front matter in md sets title/kind/owner/doc_date."""
    tmp = Path(tempfile.mkdtemp(prefix="verity-"))
    try:
        for f in files:
            name = Path(f.filename or "upload").name
            if Path(name).suffix.lower() not in SUPPORTED and name != "manifest.json":
                raise HTTPException(415, f"unsupported file type: {name}")
            data = await f.read()
            if len(data) > MAX_UPLOAD_BYTES:
                raise HTTPException(413, f"{name} too large")
            (tmp / name).write_bytes(data)
        return {"results": ingest_path(tmp, engagement_id)}
    except ValueError as e:
        raise HTTPException(400, str(e))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@app.post("/events/gcs")
async def gcs_event(request: Request):
    """Eventarc target for google.cloud.storage.object.v1.finalized.
    Object path convention gs://<bucket>/<engagement_id>/<file>. Protected by Cloud Run IAM (OIDC), not the API key."""
    if not request.headers.get("ce-type", "").startswith("google.cloud.storage.object"):
        raise HTTPException(400, "not a Cloud Storage CloudEvent")
    body = await request.json()
    bucket, name = body.get("bucket"), body.get("name", "")
    engagement = engagement_from_object(name)
    if not bucket or not engagement or Path(name).suffix.lower() not in SUPPORTED:
        return {"skipped": name}
    return {"results": ingest_path(f"gs://{bucket}/{name}", engagement)}


@app.get("/engagements", dependencies=[Auth])
def engagements():
    return sorted(db.sources().distinct("engagement_id"))


@app.get("/sources", dependencies=[Auth])
def list_sources(engagement_id: str, status: str = "active"):
    q = {"engagement_id": engagement_id} | ({} if status == "all" else {"status": status})
    return clean(list(db.sources().find(q, {"raw_text": 0}).sort("ingested_at", -1)))


@app.get("/sources/{source_id:path}", dependencies=[Auth])
def get_source(source_id: str):
    """Original source text - the UI highlights cited passages using chunk char offsets."""
    s = db.sources().find_one({"_id": source_id})
    if not s:
        raise HTTPException(404, "source not found")
    return clean(s)


@app.get("/chunks/{chunk_id:path}", dependencies=[Auth])
def get_chunk(chunk_id: str):
    c = db.chunks().find_one({"_id": chunk_id}, {"embedding": 0})
    if not c:
        raise HTTPException(404, "chunk not found")
    return clean(c)


# ----------------------------------------------------------------------------- retrieval & agents

@app.post("/search", dependencies=[Auth])
def search(body: SearchIn):
    f = Filters(engagement_id=body.engagement_id, **(body.filters.model_dump() if body.filters else {}))
    return clean([h.to_dict() for h in hybrid_search(body.query, f, body.k, body.mode)])


@app.post("/ask", dependencies=[Auth])
def ask(body: AskIn):
    try:
        return clean(service.ask(body.question, body.engagement_id, body.user_id,
                                 body.filters.model_dump(exclude_none=True) if body.filters else None,
                                 use_cache=body.use_cache))
    except LLMError as e:
        raise HTTPException(502, str(e))


@app.get("/runs", dependencies=[Auth])
def list_runs(engagement_id: str, limit: int = 20):
    cur = db.runs().find({"engagement_id": engagement_id},
                         {"question": 1, "status": 1, "totals": 1, "created_at": 1}).sort("created_at", -1)
    return clean(list(cur.limit(min(limit, 100))))


@app.get("/runs/{run_id}", dependencies=[Auth])
def get_run(run_id: str):
    r = db.runs().find_one({"_id": run_id})
    if not r:
        raise HTTPException(404, "run not found")
    return clean(r)


@app.get("/metrics", dependencies=[Auth])
def metrics(engagement_id: str, hours: float = 24 * 7):
    """LLM-native metrics over recent runs: latency percentiles, cost/request, tokens/sec, per-node breakdown."""
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    match = {"engagement_id": engagement_id, "created_at": {"$gte": since}}
    runs = list(db.runs().find(match, {"totals": 1, "status": 1}))
    if not runs:
        return {"runs": 0}
    lat = [r["totals"]["latency_ms"] for r in runs]
    statuses: dict[str, int] = {}
    for r in runs:
        statuses[r.get("status") or "unknown"] = statuses.get(r.get("status") or "unknown", 0) + 1
    nodes = list(db.runs().aggregate([
        {"$match": match}, {"$unwind": "$spans"},
        {"$group": {"_id": "$spans.name", "calls": {"$sum": 1}, "avg_ms": {"$avg": "$spans.ms"},
                    "avg_tokens_in": {"$avg": "$spans.tokens_in"}, "avg_tokens_out": {"$avg": "$spans.tokens_out"},
                    "total_cost_usd": {"$sum": "$spans.cost_usd"}}},
        {"$sort": {"avg_ms": -1}}]))
    return clean({
        "runs": len(runs), "status": statuses,
        "latency_ms": {"p50": percentile(lat, 50), "p95": percentile(lat, 95), "max": max(lat)},
        "cost_per_request_usd": sum(r["totals"]["cost_usd"] for r in runs) / len(runs),
        "total_cost_usd": sum(r["totals"]["cost_usd"] for r in runs),
        "avg_output_tokens_per_sec": sum(r["totals"]["output_tokens_per_sec"] for r in runs) / len(runs),
        "avg_tokens_per_request": sum(r["totals"]["tokens_in"] + r["totals"]["tokens_out"] for r in runs) / len(runs),
        "nodes": nodes,
    })


@app.post("/eval", dependencies=[Auth])
def run_eval(body: EvalIn):
    from eval.run_eval import run

    try:
        return clean(run(body.engagement_id, list(body.modes), body.k, body.agent, body.judge))
    except LLMError as e:
        raise HTTPException(502, str(e))


@app.get("/eval/history", dependencies=[Auth])
def eval_history(engagement_id: str, limit: int = 10):
    cur = db.eval_runs().find({"engagement_id": engagement_id}, {"summary": 1, "config": 1, "created_at": 1})
    return clean(list(cur.sort("created_at", -1).limit(limit)))


# ----------------------------------------------------------------------------- memory

@app.post("/feedback", dependencies=[Auth])
def feedback(body: FeedbackIn):
    """Analyst feedback / client statements become engagement memory that shapes the next brief."""
    try:
        return clean(memory.remember(body.engagement_id, body.user_id, body.type, body.content, body.ttl_days,
                                     body.supersedes))
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/memories", dependencies=[Auth])
def memories(engagement_id: str, status: str = "active"):
    q = {"engagement_id": engagement_id} | ({} if status == "all" else {"status": status})
    return clean([{**m, "_id": str(m["_id"])} for m in db.memories().find(q, {"embedding": 0})
                  .sort("created_at", -1)])


@app.delete("/memories/{memory_id}", dependencies=[Auth])
def delete_memory(memory_id: str):
    if not ObjectId.is_valid(memory_id):
        raise HTTPException(400, "invalid memory id")
    memory.forget(memory_id)
    return {"deleted": memory_id}
