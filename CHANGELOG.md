# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/).

## [1.1.0] - 2026-11-22

### Added
- Structured JSON logging in Cloud Logging format, correlated by request ID and trace ID
- Request-context middleware: `X-Request-ID`, access log, security headers (CSP, HSTS, nosniff, frame-deny)
- Per-client token-bucket rate limiting with `Retry-After`
- `/healthz` (liveness), `/readyz` (readiness, MongoDB ping) and `/version`
- Startup configuration validation: `ENV=production` refuses unsafe settings
- `GEMINI_BACKEND=auto|vertex|api`
- MongoDB connection-pool and timeout settings
- Graceful shutdown
- Terraform for GCP:
  - Cloud Run service and job, Artifact Registry, Secret Manager, GCS, Eventarc
  - Workload Identity Federation
  - alert policies and a billing budget
- CI/CD:
  - lint and type-check jobs; a test matrix with a coverage gate; the eval gate with a report artifact
  - pip-audit, gitleaks, a container build with smoke test and Trivy scan, Terraform validation
- Tag-driven deploy: a revision with no traffic, then a smoke test, then the traffic shift
- Multi-stage non-root container with a HEALTHCHECK
- docker-compose with `mongodb-atlas-local`
- Docs: design doc, ADRs, operations runbook with SLOs, security policy, contributing guide
- `pyproject.toml`, ruff, mypy, pre-commit, Makefile
- 18 new tests, covering the gateway fallback and circuit breaker, rate limiting, probes and redaction

### Changed
- LLM errors return a stable 502 body with a request ID, and unhandled errors never expose internals

## [1.0.0] - 2026-11-21

### Added
- 54-question golden set in 9 categories, including cross-tenant leakage and prompt-injection cases, plus a
  second-tenant corpus
- 95% bootstrap confidence intervals and per-category metrics
- Retrieval ablations (`eval/ablate.py`)
- Persistent content-addressed embedding cache
- Resumable agent evaluation

### Fixed
- Source and chunk IDs are now scoped per engagement. Identically named files from two clients used to collide.

## [0.9.0] - 2026-11-20

### Added
- LangGraph multi-agent workflow:
  - supervisor with hierarchical delegation
  - ReAct researcher
  - analyst
  - self-reflection verifier with a retry edge
  - writer, or a discovery pack when the evidence is missing
- Hybrid retrieval (Atlas `$vectorSearch` + `$search` with RRF), metadata filters and safe aggregations over
  structured records
- Ingestion for md, txt, html, pdf, csv and json: versioning, redaction, heading-aware chunking, and GCS with
  Eventarc
- Engagement memory: TTL, supersede, forget
- Semantic answer cache invalidated by a corpus fingerprint
- Tracing with per-node latency, tokens, tokens/sec and cost; OpenTelemetry export to Cloud Trace
- LLM gateway:
  - Gemini on Vertex AI or the Gemini API, plus Claude on Vertex AI
  - fallback chain and circuit breaker
- FastAPI service and web UI
- Evaluation harness with an LLM judge and a CI regression gate
- First real-model evaluation with Gemini
