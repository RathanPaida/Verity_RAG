# syntax=docker/dockerfile:1.7
# ---- build stage: resolve and compile wheels once --------------------------------------------
FROM python:3.15-slim AS build
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /build
COPY requirements.txt .
RUN pip wheel --wheel-dir /wheels -r requirements.txt

# ---- runtime stage: no compilers, non-root, read-only friendly ------------------------------
FROM python:3.15-slim AS runtime
ARG BUILD_SHA=dev
LABEL org.opencontainers.image.title="verity" \
      org.opencontainers.image.description="Evidence-grounded due-diligence agents (LangGraph + MongoDB Atlas + Gemini)" \
      org.opencontainers.image.source="https://github.com/RathanPaida/Verity_RAG" \
      org.opencontainers.image.revision="${BUILD_SHA}"
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PORT=8080 BUILD_SHA=${BUILD_SHA} \
    LOG_FORMAT=json EMBEDDING_CACHE_PATH=/tmp/verity/embeddings.sqlite
WORKDIR /app
COPY --from=build /wheels /wheels
RUN pip install --no-cache-dir --no-index --find-links=/wheels /wheels/* && rm -rf /wheels \
 && useradd --system --uid 10001 --no-create-home appuser
COPY verity ./verity
COPY scripts ./scripts
COPY eval ./eval
COPY data/sample ./data/sample
USER 10001
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s --start-period=20s --retries=3 \
  CMD python -c "import os,urllib.request;urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\",\"8080\")}/healthz',timeout=2)"
# Cloud Run sends SIGTERM and allows 10 s; uvicorn drains in-flight requests within that window
CMD ["sh", "-c", "exec uvicorn verity.api.main:app --host 0.0.0.0 --port ${PORT} --workers ${WEB_CONCURRENCY:-2} --proxy-headers --forwarded-allow-ips='*' --timeout-graceful-shutdown 8"]
