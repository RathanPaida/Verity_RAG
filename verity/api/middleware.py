"""HTTP middleware: request context + access log, security headers, per-client rate limiting."""
from __future__ import annotations

import hashlib
import logging
import re
import threading
import time
import uuid

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from ..config import settings
from ..observability.logs import request_id_var, trace_var

access_log = logging.getLogger("verity.access")

_RID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
        "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
_DOCS = ("/docs", "/redoc", "/openapi.json")  # Swagger UI loads its assets from a CDN


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Assigns a request ID (honours a well-formed inbound X-Request-ID), binds the Cloud Trace ID for log
    correlation, adds security headers and writes one structured access-log line per request."""

    async def dispatch(self, request: Request, call_next):
        rid = request.headers.get("x-request-id", "")
        rid = rid if _RID.match(rid) else uuid.uuid4().hex[:16]
        trace = request.headers.get("x-cloud-trace-context", "").split("/")[0] or None
        t_rid, t_trace = request_id_var.set(rid), trace_var.set(trace)
        t0 = time.perf_counter()
        status = 500
        try:
            try:
                response = await call_next(request)
            except Exception:
                # Never leak stack traces to clients; the request ID links the response to the logged traceback.
                logging.getLogger("verity.api").exception("unhandled error on %s %s", request.method,
                                                          request.url.path)
                response = JSONResponse({"detail": "internal error", "request_id": rid}, status_code=500)
            status = response.status_code
        finally:
            ms = round((time.perf_counter() - t0) * 1000, 1)
            if request.url.path not in ("/healthz", "/readyz"):  # keep probe noise out of the logs
                access_log.info("%s %s %s %.0fms", request.method, request.url.path, status, ms,
                                extra={"httpRequest": {"requestMethod": request.method,
                                                       "requestUrl": request.url.path, "status": status,
                                                       "latency": f"{ms / 1000:.3f}s"}})
            request_id_var.reset(t_rid)
            trace_var.reset(t_trace)
        response.headers["X-Request-ID"] = rid
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        if not request.url.path.startswith(_DOCS):
            response.headers["Content-Security-Policy"] = _CSP
        if request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https":
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response


class TokenBucket:
    """Per-key token bucket. In-process: on Cloud Run each instance enforces its own budget, which bounds
    the cost of a runaway client per instance; put Cloud Armor / API Gateway in front for a global limit."""

    def __init__(self, rate_per_minute: int, burst: int | None = None, max_keys: int = 10_000):
        self.rate = rate_per_minute / 60.0
        self.capacity = float(burst or max(1, rate_per_minute))
        self.max_keys = max_keys
        self._buckets: dict[str, tuple[float, float]] = {}
        self._lock = threading.Lock()

    def take(self, key: str, now: float | None = None) -> float:
        """Consume one token. Returns 0 if allowed, else seconds until a token is available."""
        now = time.monotonic() if now is None else now
        with self._lock:
            tokens, last = self._buckets.get(key, (self.capacity, now))
            tokens = min(self.capacity, tokens + (now - last) * self.rate)
            if tokens >= 1:
                self._buckets[key] = (tokens - 1, now)
                wait = 0.0
            else:
                self._buckets[key] = (tokens, now)
                wait = (1 - tokens) / self.rate
            if len(self._buckets) > self.max_keys:  # bounded memory under key-spraying
                for k in list(self._buckets)[: self.max_keys // 10]:
                    self._buckets.pop(k, None)
            return wait


LIMITED = {("POST", "/ask"), ("POST", "/eval"), ("POST", "/ingest"), ("POST", "/search"), ("POST", "/feedback")}


def client_key(request: Request) -> str:
    key = request.headers.get("x-api-key")
    if key:
        return "key:" + hashlib.sha256(key.encode()).hexdigest()[:16]
    fwd = request.headers.get("x-forwarded-for", "")
    return "ip:" + (fwd.split(",")[0].strip() or (request.client.host if request.client else "unknown"))


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, bucket: TokenBucket | None = None):
        super().__init__(app)
        self.bucket = bucket

    def _bucket(self) -> TokenBucket | None:
        if settings.rate_limit_per_minute <= 0:
            return None
        if self.bucket is None or self.bucket.rate != settings.rate_limit_per_minute / 60.0:
            self.bucket = TokenBucket(settings.rate_limit_per_minute)
        return self.bucket

    async def dispatch(self, request: Request, call_next):
        bucket = self._bucket()
        if bucket and (request.method, request.url.path) in LIMITED:
            wait = bucket.take(client_key(request))
            if wait > 0:
                return JSONResponse({"detail": "rate limit exceeded", "retry_after_s": round(wait, 1)},
                                    status_code=429, headers={"Retry-After": str(max(1, round(wait)))})
        return await call_next(request)
