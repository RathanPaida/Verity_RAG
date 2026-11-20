"""Google Cloud Storage source: download gs:// objects to a temp dir, keeping the gs:// URI as provenance.

Bucket layout convention:  gs://<bucket>/<engagement_id>/<file>
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from .loaders import SUPPORTED


def parse_gs(uri: str) -> tuple[str, str]:
    if not uri.startswith("gs://"):
        raise ValueError(f"not a gs:// URI: {uri}")
    bucket, _, prefix = uri[5:].partition("/")
    return bucket, prefix


def engagement_from_object(name: str) -> str | None:
    parts = name.split("/")
    return parts[0] if len(parts) >= 2 and parts[0] else None


def download(uri: str) -> tuple[Path, dict[str, str]]:
    """Download one object or every supported object under a prefix. Returns (dir, {filename: gs_uri})."""
    from google.cloud import storage

    bucket_name, prefix = parse_gs(uri)
    bucket = storage.Client().bucket(bucket_name)
    tmp = Path(tempfile.mkdtemp(prefix="verity-gcs-"))
    uris: dict[str, str] = {}
    blobs = [bucket.blob(prefix)] if Path(prefix).suffix else list(bucket.list_blobs(prefix=prefix))
    for b in blobs:
        name = Path(b.name).name
        if Path(name).suffix.lower() not in SUPPORTED and name != "manifest.json":
            continue
        b.download_to_filename(tmp / name)
        uris[name] = f"gs://{bucket_name}/{b.name}"
    return tmp, uris
