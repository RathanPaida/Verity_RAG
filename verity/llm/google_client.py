"""One google-genai client for generation + embeddings.

Backend selection:
  GOOGLE_CLOUD_PROJECT set -> Vertex AI (ADC / service account; production on GCP)
  otherwise                -> Gemini Developer API (GEMINI_API_KEY or GOOGLE_API_KEY)
Retries with exponential backoff on 429 / 5xx are handled by the SDK.
"""
from __future__ import annotations

from functools import lru_cache

from ..config import settings

RETRY_CODES = [408, 500, 502, 503, 504]  # 429 is handled in the gateway (retryDelay, then fallback)


def backend() -> str:
    """GEMINI_BACKEND=vertex|api forces a backend; auto picks Vertex AI whenever a GCP project is configured.
    `api` lets a Cloud Run deployment use a Gemini API key while still running on GCP."""
    if settings.gemini_backend == "vertex":
        return "vertex"
    if settings.gemini_backend == "api":
        return "gemini-api"
    return "vertex" if settings.gcp_project else "gemini-api"


@lru_cache(maxsize=1)
def gemini_client():
    from google import genai
    from google.genai import types

    retry = types.HttpRetryOptions(attempts=settings.llm_retry_attempts, initial_delay=2.0, max_delay=30.0,
                                   exp_base=2.0, http_status_codes=RETRY_CODES)
    http = types.HttpOptions(timeout=settings.llm_timeout_ms, retry_options=retry)
    if backend() == "vertex":
        return genai.Client(vertexai=True, project=settings.gcp_project, location=settings.gcp_location,
                            http_options=http)
    return genai.Client(http_options=http)
