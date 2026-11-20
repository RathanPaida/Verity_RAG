"""Run the full app offline - no Atlas, no GCP: in-memory MongoDB, hash embeddings, heuristic 'fake' LLM,
sample corpus auto-ingested. Open http://localhost:8000

    python -m scripts.serve_demo [--port 8000]
"""
import argparse
import os

DEMO_ENV = {"MONGODB_URI": "mongomock://", "LLM_PROVIDER": "fake", "EMBEDDING_PROVIDER": "hash",
            "EMBEDDING_DIM": "256", "AUTO_INGEST_SAMPLE": "1", "CHECKPOINTER": "memory", "VERITY_API_KEY": ""}

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8000)
    a = ap.parse_args()
    os.environ.update(DEMO_ENV)  # must happen before verity.config is imported
    import uvicorn

    uvicorn.run("verity.api.main:app", host="127.0.0.1", port=a.port)
