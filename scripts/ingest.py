"""Ingest a file or folder (also the entrypoint for the Cloud Run ingestion Job).

    python -m scripts.ingest data/sample --engagement acme-payments
    python -m scripts.ingest gs://my-bucket/acme-payments --engagement acme-payments
"""
import argparse
import json

from verity.observability.logs import configure_logging


def main() -> None:
    configure_logging()
    from verity.ingest.pipeline import ingest_path

    ap = argparse.ArgumentParser(description="Ingest a file, folder or gs:// prefix into an engagement")
    ap.add_argument("path")
    ap.add_argument("--engagement", required=True)
    a = ap.parse_args()
    for r in ingest_path(a.path, a.engagement):
        print(json.dumps(r, default=str))


if __name__ == "__main__":
    main()
