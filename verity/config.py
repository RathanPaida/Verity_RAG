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
    # requests per minute per client (API key, else IP) on expensive routes; 0 disables
    rate_limit_per_minute = _i("RATE_LIMIT_PER_MINUTE", 30)
    cors_origins = [o.strip() for o in os.getenv("CORS_ORIGINS", "").split(",") if o.strip()]
    max_upload_mb = _i("MAX_UPLOAD_MB", 20)

    # Runtime
    env = os.getenv("ENV", "development")  # development | test | production
    log_level = os.getenv("LOG_LEVEL", "INFO")
    log_format = os.getenv("LOG_FORMAT", "json" if os.getenv("K_SERVICE") else "text")  # K_SERVICE = Cloud Run
    build_sha = os.getenv("BUILD_SHA", "dev")
    # Gemini backend: auto (Vertex AI when GOOGLE_CLOUD_PROJECT is set) | vertex | api (GEMINI_API_KEY)
    gemini_backend = os.getenv("GEMINI_BACKEND", "auto")
    mongo_timeout_ms = _i("MONGO_TIMEOUT_MS", 5000)
    mongo_max_pool = _i("MONGO_MAX_POOL", 50)

    def problems(self) -> list[str]:
        """Configuration errors that must stop a production deployment (checked at startup)."""
        out: list[str] = []
        if self.llm_provider not in ("gemini", "claude", "fake"):
            out.append(f"LLM_PROVIDER={self.llm_provider!r} is not one of gemini|claude|fake")
        if self.embedding_provider not in ("gemini", "vertex", "hash"):
            out.append(f"EMBEDDING_PROVIDER={self.embedding_provider!r} is not one of gemini|vertex|hash")
        if self.gemini_backend not in ("auto", "vertex", "api"):
            out.append(f"GEMINI_BACKEND={self.gemini_backend!r} is not one of auto|vertex|api")
        if self.gemini_backend == "vertex" and not self.gcp_project:
            out.append("GEMINI_BACKEND=vertex needs GOOGLE_CLOUD_PROJECT")
        if self.checkpointer not in ("mongodb", "memory"):
            out.append(f"CHECKPOINTER={self.checkpointer!r} is not one of mongodb|memory")
        if self.env == "production":
            if not self.api_key:
                out.append("VERITY_API_KEY must be set in production")
            if self.mongodb_uri.startswith("mongomock://"):
                out.append("MONGODB_URI=mongomock:// is not allowed in production")
            if self.llm_provider == "fake" or self.embedding_provider == "hash":
                out.append("offline providers (fake LLM / hash embeddings) are not allowed in production")
        return out

    def summary(self) -> dict:
        """Non-secret view of the effective configuration (logged at startup, served by /version)."""
        return {"env": self.env, "build": self.build_sha, "llm": self.llm_provider, "model_fast": self.model_fast,
                "model_strong": self.model_strong, "fallbacks": self.model_fallbacks,
                "embeddings": f"{self.embedding_provider}:{self.embedding_model}/{self.embedding_dim}",
                "gemini_backend": self.gemini_backend, "checkpointer": self.checkpointer,
                "auth": bool(self.api_key), "rate_limit_per_minute": self.rate_limit_per_minute}


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
