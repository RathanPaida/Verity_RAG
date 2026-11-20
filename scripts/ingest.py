"""Ingest a file or folder (also the entrypoint for the Cloud Run ingestion Job).

    python -m scripts.ingest data/sample --engagement acme-payments
"""
import argparse
import json
import logging

from verity.ingest.pipeline import ingest_path

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    ap.add_argument("--engagement", required=True)
    a = ap.parse_args()
    for r in ingest_path(a.path, a.engagement):
        print(json.dumps(r, default=str))
