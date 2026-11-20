"""Ingestion pipeline: load -> redact -> dedupe/version -> chunk -> embed -> store sources + chunks."""
from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

from .. import db
from ..config import settings
from ..embeddings import get_embedder
from ..security import redact
from .chunker import chunk_records, chunk_text
from .loaders import Document, load_file, load_folder

log = logging.getLogger("verity.ingest")


def _hash(doc: Document) -> str:
    payload = doc.text if doc.records is None else json.dumps(doc.records, default=str, sort_keys=True)
    return "sha256:" + hashlib.sha256(payload.encode()).hexdigest()


def ingest_document(doc: Document, engagement_id: str) -> dict:
    now = datetime.now(timezone.utc)
    if doc.records is None:
        doc.text, findings = redact(doc.text)
    else:
        findings = []
    content_hash = _hash(doc)

    latest = db.sources().find_one({"engagement_id": engagement_id, "source_key": doc.source_key},
                                   sort=[("version", -1)])
    if latest and latest["content_hash"] == content_hash:
        return {"source_id": latest["_id"], "status": "unchanged", "chunks": latest["chunk_count"]}
    version = (latest["version"] + 1) if latest else 1
    # ids are scoped per engagement so two clients can import files with the same name
    source_id = f"{engagement_id}:{doc.source_key}@v{version}"

    pieces = chunk_records(doc.records, doc.title) if doc.records is not None \
        else chunk_text(doc.text, settings.chunk_tokens)
    if not pieces:
        return {"source_id": source_id, "status": "empty", "chunks": 0}

    emb = get_embedder()
    vectors = emb.embed([p.text for p in pieces], kind="document")

    if latest:  # older versions stay for audit / diff but leave retrieval
        db.sources().update_many({"engagement_id": engagement_id, "source_key": doc.source_key,
                                  "status": "active"}, {"$set": {"status": "superseded"}})
        db.chunks().update_many({"engagement_id": engagement_id, "source_id": {"$regex": f"^{re.escape(engagement_id)}:{re.escape(doc.source_key)}@"},
                                 "status": "active"}, {"$set": {"status": "superseded"}})

    db.sources().insert_one({
        "_id": source_id, "source_key": doc.source_key, "version": version, "engagement_id": engagement_id,
        "title": doc.title, "kind": doc.kind, "owner": doc.owner, "doc_date": doc.doc_date, "mime": doc.mime,
        "uri": doc.uri, "tags": doc.tags, "content_hash": content_hash,
        "raw_text": doc.text if doc.records is None else None, "record_count": len(doc.records or []),
        "chunk_count": len(pieces), "privacy_findings": findings, "status": "active", "ingested_at": now,
    })
    db.chunks().insert_many([{
        "_id": f"{source_id}#{i}", "source_id": source_id, "engagement_id": engagement_id, "kind": doc.kind,
        "owner": doc.owner, "doc_date": doc.doc_date, "title": doc.title, "section_path": p.section_path,
        "chunk_index": i, "char_start": p.char_start, "char_end": p.char_end, "text": p.text,
        "fields": p.fields, "embedding": v, "embedding_model": emb.name, "status": "active", "ingested_at": now,
    } for i, (p, v) in enumerate(zip(pieces, vectors))])
    log.info("ingested %s (%d chunks)", source_id, len(pieces))
    return {"source_id": source_id, "status": "updated" if latest else "created", "chunks": len(pieces),
            "privacy_findings": findings}


def ingest_path(path: str | Path, engagement_id: str) -> list[dict]:
    """Ingest a local file/folder or a gs:// object/prefix."""
    if str(path).startswith("gs://"):
        import shutil

        from .gcs import download

        tmp, uris = download(str(path))
        try:
            docs = load_folder(tmp)
            for d in docs:
                d.uri = uris.get(Path(d.uri).name, d.uri)  # provenance = original gs:// object
            return [ingest_document(d, engagement_id) for d in docs]
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    p = Path(path)
    docs = load_folder(p) if p.is_dir() else [load_file(p)]
    return [ingest_document(d, engagement_id) for d in docs]
