"""Central settings (env / .env). In Cloud Run, secrets come from Secret Manager as env vars."""
import os

from dotenv import load_dotenv

load_dotenv()


def _f(name, default):
    return float(os.getenv(name, default))


def _i(name, default):
    return int(os.getenv(name, default))


class Settings:
    # MongoDB
    mongodb_uri = os.getenv("MONGODB_URI", "mongodb://localhost:27017/?directConnection=true")
    mongodb_db = os.getenv("MONGODB_DB", "verity")
    vector_index = "chunks_vector"
    text_index = "chunks_text"

    # Google Cloud / Vertex AI
    gcp_project = os.getenv("GOOGLE_CLOUD_PROJECT", "")
    gcp_location = os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1")

    # LLM gateway: gemini | claude | fake
    llm_provider = os.getenv("LLM_PROVIDER", "gemini")
    model_fast = os.getenv("MODEL_FAST", "gemini-3.8-flash")   # supervisor / researcher / verifier
    model_strong = os.getenv("MODEL_STRONG", "gemini-3.1-pro-preview")  # analyst / writer
    claude_model = os.getenv("CLAUDE_MODEL", "claude-opus-5-5")
    # Ordered fallbacks used on 429 / 5xx (quota exhausted, model overloaded); comma-separated
    model_fallbacks = [m.strip() for m in os.getenv(
        "MODEL_FALLBACKS", "gemini-3-flash-preview,gemini-3.5-flash-lite").split(",") if m.strip()]
    llm_retry_attempts = _i("LLM_RETRY_ATTEMPTS", 3)      # per model, before falling back
    llm_timeout_ms = _i("LLM_TIMEOUT_MS", 120_000)
    fast_thinking_level = os.getenv("FAST_THINKING_LEVEL", "low")  # minimal | low | medium | high (Gemini 3)

    # Embeddings: vertex | hash
    embedding_provider = os.getenv("EMBEDDING_PROVIDER", "gemini")  # gemini | vertex (alias) | hash
    embedding_model = os.getenv("EMBEDDING_MODEL", "gemini-embedding-001")
    embedding_dim = _i("EMBEDDING_DIM", 768)
    # persistent content-addressed embedding cache; empty string disables
    embedding_cache_path = os.getenv("EMBEDDING_CACHE_PATH", ".cache/embeddings.sqlite")

    # Chunking
    chunk_tokens = _i("CHUNK_TOKENS", 350)

    # Retrieval
    top_k = _i("TOP_K", 8)
    rrf_k = _i("RRF_K", 60)
    vector_weight = _f("VECTOR_WEIGHT", 1.0)
    text_weight = _f("TEXT_WEIGHT", 1.0)
    min_evidence_score = _f("MIN_EVIDENCE_SCORE", 0.02)  # fused RRF score below this = weak evidence

    # Agent budgets
    max_react_steps = _i("MAX_REACT_STEPS", 4)
    max_verify_retries = _i("MAX_VERIFY_RETRIES", 1)
    max_subquestions = _i("MAX_SUBQUESTIONS", 4)
    checkpointer = os.getenv("CHECKPOINTER", "mongodb")  # mongodb | memory

    # Semantic answer cache (cost-per-request optimisation); 0 disables
    cache_ttl_hours = _f("CACHE_TTL_HOURS", 24)
    cache_min_similarity = _f("CACHE_MIN_SIMILARITY", 0.97)

    # Demo: ingest data/sample at startup when the DB is empty (handy with mongomock://)
    auto_ingest_sample = os.getenv("AUTO_INGEST_SAMPLE", "0") == "1"
    demo_engagement = os.getenv("DEMO_ENGAGEMENT", "acme-payments")

    # Security
    api_key = os.getenv("VERITY_API_KEY", "")  # empty = auth disabled (local dev only)


settings = Settings()

# USD per 1M tokens (input, output), standard paid tier, prompts <= 200k tokens.
# Source: Gemini API pricing page, checked 2026-11-20. gemini-3.8-flash rates apply through 2026-12-31
# ($1.50 / $7.50 from 2027-01-01). Re-check before quoting costs; override with PRICE_<MODEL>="in,out".
PRICES = {
    "gemini-3.8-flash": (0.75, 3.75),
    "gemini-3.6-flash": (0.75, 3.75),
    "gemini-3-flash-preview": (0.50, 3.00),
    "gemini-3.5-flash-lite": (0.30, 2.50),
    "gemini-3.1-pro-preview": (2.00, 12.00),
    "gemini-2.5-flash": (0.30, 2.50),
    "gemini-2.5-pro": (1.25, 10.00),
    "gemini-embedding-001": (0.15, 0.0),
    "claude-opus-5-5": (4.00, 20.00),
    "fake": (0.0, 0.0),
}
for _k, _v in os.environ.items():
    if _k.startswith("PRICE_"):
        _i_, _o_ = (float(x) for x in _v.split(","))
        PRICES[_k[6:].lower().replace("_", "-")] = (_i_, _o_)
