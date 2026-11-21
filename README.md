# Verity

[![ci](https://github.com/RathanPaida/Verity_RAG/actions/workflows/ci.yml/badge.svg)](https://github.com/RathanPaida/Verity_RAG/actions/workflows/ci.yml)
![python](https://img.shields.io/badge/python-3.10%20%7C%203.12-blue)
![license](https://img.shields.io/badge/license-MIT-green)

**Evidence-grounded engineering due-diligence agents.**

Verity reads a client's architecture docs, postmortems, security reviews, repository summary and structured
incident/SLO records, and writes a **cited engineering risk brief**:

- Every finding links to the exact passage it came from.
- Contradictions between sources are flagged.
- When the evidence isn't there, it **abstains** and writes the questions to ask in the next client session.

The stack:

| Layer | Technology |
|---|---|
| Orchestration | **LangGraph** multi-agent graph (supervisor → ReAct researcher → analyst → self-reflection verifier → writer) |
| Retrieval | **MongoDB Atlas** Vector Search + Atlas Search, fused with RRF |
| Models | **Gemini** on Vertex AI or the Gemini API, behind a resilient model gateway |
| Serving | FastAPI and a web UI on **Cloud Run** |
| Infrastructure | Terraform for Cloud Run, Eventarc, GCS, Secret Manager, Workload Identity Federation, Cloud Monitoring |

```
 GCS upload ─▶ Eventarc ─▶ /events/gcs ─┐
 UI / API  ─▶ /ingest ──────────────────┤ redact → version → chunk (section paths, offsets) → embed ─▶ Atlas
                                        │
 /ask ─▶ rate limit ─▶ semantic cache ─▶ LangGraph ──────────────────────────────────────────────────────┐
           recall memory → SUPERVISOR (plan, delegate) → RESEARCHER (ReAct: hybrid_search · query_records  │
           · expand_context) → ANALYST (claims, conflicts, gaps) → VERIFIER ─┬─ retry ─▶ RESEARCHER       │
                                                                             ├─ weak  ─▶ DISCOVERY PACK    │
                                                                             └─ ok    ─▶ WRITER (cited)    │
           every node/tool = span (latency · tokens · $ · model) → runs + Cloud Trace; state → MongoDBSaver│
```

## Highlights

**Grounded answers**
- Citations are validated against the retrieved evidence set.
- The verifier checks that each claim is entailed by its cited passages, and loops back to research when it isn't.
- Weak evidence produces a discovery pack instead of a guess.

**Hybrid retrieval** over unstructured documents and structured records:
- vector and BM25 search with RRF fusion and metadata pre-filters
- whitelisted aggregation specs over CSV/JSON rows (never raw MQL)

**Multi-tenant by construction**
- IDs are scoped to the engagement.
- Every query carries a mandatory `engagement_id` filter.
- Leakage is measured by the evaluation.

**Resilient model gateway**
- fast and strong tiers with an ordered fallback chain
- a per-model circuit breaker that honours 429 `retryDelay`
- cost accounting that includes thinking tokens

**Evaluation as a release gate**
- a 54-question golden set in 9 categories, including cross-tenant leakage and prompt injection
- 95% bootstrap confidence intervals, an LLM judge and retrieval ablations
- a CI regression gate

**Production operations**
- structured logs correlated with request and trace IDs
- liveness and readiness probes, rate limiting, security headers
- startup config validation, SLOs with alert policies and runbooks, tag-driven deploys

## Quick start (offline, no keys)

```bash
python -m venv .venv && . .venv/bin/activate     # Windows: .venv\Scripts\activate
make install                                     # or: pip install -r requirements-dev.txt
make demo                                        # http://localhost:8000, sample corpus auto-ingested
make check                                       # ruff · mypy · 53 tests + coverage gate · eval gate
```

Offline mode uses in-memory MongoDB, hash embeddings and a deterministic stand-in for the LLM. It exercises
every code path. Answer quality numbers come from real-model runs (see [Results](#results)).

**With real Atlas Search locally:**

```bash
cp .env.example .env    # add GEMINI_API_KEY
docker compose up       # starts mongodb-atlas-local + the API
```

**With Gemini and Atlas:**

```bash
cp .env.example .env    # MONGODB_URI, GEMINI_API_KEY or GOOGLE_CLOUD_PROJECT
python -m scripts.setup_db
python -m scripts.ingest data/sample --engagement acme-payments
make run
```

## Deploy to Google Cloud

```bash
cd infra/terraform && terraform init -backend-config="bucket=<state-bucket>" && terraform apply
# add the secret values, set the repo variables printed by `terraform output`, then:
git tag v1.1.0 && git push --tags
```

The tag triggers [deploy.yml](.github/workflows/deploy.yml):
1. Build the image and push it to Artifact Registry, authenticating keylessly through WIF.
2. Deploy a Cloud Run revision that receives no traffic.
3. Smoke test `/readyz` and `/version` on the revision's tagged URL.
4. Shift traffic, all at once or as a canary.

Details: [infra/README.md](infra/README.md) and [docs/OPERATIONS.md](docs/OPERATIONS.md).

## API

| Method | Path | Purpose |
|---|---|---|
| POST | `/ask` | agent run: brief, citations, conflicts, discovery pack, plan, tool log, verification, spans, cost |
| POST | `/search` | `vector` / `text` / `hybrid`, with metadata filters |
| POST | `/ingest` | multipart upload (md/txt/html/pdf/csv/json), versioned and redacted |
| POST | `/events/gcs` | Eventarc target for GCS object-finalize events (Cloud Run IAM) |
| GET | `/engagements`, `/sources`, `/sources/{id}`, `/chunks/{id}` | browse the corpus and exact passages |
| GET | `/runs`, `/runs/{id}`, `/metrics` | traces and LLM metrics (p50/p95, cost/request, tokens/sec, per node) |
| POST/GET | `/eval`, `/eval/history` | golden-set evaluation |
| POST/GET/DELETE | `/feedback`, `/memories`, `/memories/{id}` | engagement memory |
| GET | `/healthz`, `/readyz`, `/version` | liveness, readiness (MongoDB ping), build and config |

Authentication is `X-API-Key`, backed by Secret Manager. Responses carry `X-Request-ID`. Expensive routes are
rate limited per client. OpenAPI is served at `/docs`.

## Results

### Evaluation design ([eval/golden.jsonl](eval/golden.jsonl))

54 questions in 9 categories:

| keyword | semantic | multi-source | structured | conflict | temporal | unanswerable | cross-tenant | injection |
|---|---|---|---|---|---|---|---|---|
| 8 | 10 | 10 | 8 | 4 | 3 | 5 | 3 | 3 |

Each question carries:
- expected source IDs
- facts the answer must contain
- strings it must never contain
- whether the sources conflict

The corpus:
- 14 sources for the first client.
- A second client whose documents hold the answers to the first client's cross-tenant questions.
- A partner document with a planted prompt injection.

### Retrieval with Gemini embeddings (46 answerable questions, [docs/ABLATIONS.md](docs/ABLATIONS.md))

| mode | recall@5 [95% CI] | MRR [95% CI] |
|---|---|---|
| vector | 0.98 [0.95–1.00] | 0.95 [0.90–0.99] |
| keyword (BM25) | 0.90 [0.81–0.97] | 0.83 [0.73–0.91] |
| hybrid (RRF 1.0 / 0.5) | 0.98 [0.95–1.00] | 0.95 [0.90–0.99] |

Cross-tenant leakage was 0 passages in every configuration.

### Agent with Gemini (first 12-question run, [docs/RESULTS.md](docs/RESULTS.md))

| abstention accuracy | citation precision | faithfulness (LLM judge) | mean cost / question |
|---|---|---|---|
| 12/12 | 0.84 | 0.88 | $0.006 |

The full 54-question agent evaluation is resumable:

```bash
python -m eval.run_eval --ingest data/sample --agent --judge --resume
```

CI gates every change on the offline baseline ([ADR 0005](docs/adr/0005-offline-deterministic-mode.md)).

## Documentation

| Doc | Contents |
|---|---|
| [docs/DESIGN.md](docs/DESIGN.md) | architecture, agent graph, data model, request lifecycle, risks, roadmap |
| [docs/adr/](docs/adr/) | architecture decision records |
| [docs/OPERATIONS.md](docs/OPERATIONS.md) | SLOs, signals, alerts and runbooks, deploy and rollback, routine tasks, retention |
| [SECURITY.md](SECURITY.md) | threat model and controls, secrets handling, reporting |
| [docs/FIELD_NOTES.md](docs/FIELD_NOTES.md) | issues found running against real models and quotas, and the fix for each |
| [docs/DISCOVERY.md](docs/DISCOVERY.md) | problem framing, users, success metrics |
| [CONTRIBUTING.md](CONTRIBUTING.md), [CHANGELOG.md](CHANGELOG.md) | workflow, conventions, releases |

## Layout

```
verity/   config · db · embeddings · security · service
          api/           main (routes, probes) · middleware (request context, security headers, rate limit) · static UI
          agents/        state · prompts · nodes · graph (LangGraph + MongoDBSaver)
          retrieval/     hybrid (RRF + fallbacks) · structured (safe aggregations)
          ingest/        loaders · chunker · pipeline · gcs
          llm/           gateway (tiers, fallback chain, circuit breaker) · google_client (Vertex / API)
          memory/        engagement memory
          observability/ tracing (spans, cost, OpenTelemetry) · logs (Cloud Logging JSON)
eval/     golden set · metrics · LLM judge · run_eval (baseline + gate) · ablate
infra/    terraform (Cloud Run, Eventarc, GCS, Secret Manager, WIF, monitoring)
scripts/  setup_db · ingest · ask · showcase · load_test · explain_query · serve_demo
tests/    unit · end-to-end · API · production hardening
```

## License

MIT
