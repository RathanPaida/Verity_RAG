# Verity: evidence-grounded due-diligence agents

A team of agents that turns a client's architecture docs, postmortems, security reviews, repository summary and
structured incident records into a **cited engineering risk brief**. It flags **contradictions between sources**
and **refuses to guess** when the evidence isn't there, producing questions for the next client discovery session
instead. It remembers each engagement, and every run is traced with latency, tokens, tokens/sec and $ cost and
evaluated against a golden set.

Stack: **LangGraph** multi-agent (MongoDB checkpointer) · **Gemini** via Vertex AI or the Gemini API (or Claude on Vertex AI) ·
**MongoDB Atlas** Vector Search + Atlas Search (hybrid, RRF) · FastAPI + web UI on **Cloud Run** · GCS + Eventarc
ingestion · Secret Manager · Cloud Trace · GitHub Actions eval gate.

📊 **[docs/RESULTS.md](docs/RESULTS.md) shows a real run with Gemini**: briefs, citations, the conflict it caught, the refusal,
the memory effect, and the evaluation.

**[PLAN.md](PLAN.md)** covers the JD mapping, features, architecture, data model and per-phase status;
**[docs/DISCOVERY.md](docs/DISCOVERY.md)** covers the problem framing and success metrics.

## 1. Try it in 2 minutes (fully offline)

No Atlas, no GCP, no keys. This uses in-memory MongoDB, hash embeddings and a heuristic stand-in for the LLM, and
the sample corpus is auto-ingested.

```bash
cd verity
python -m venv .venv && .venv\Scripts\activate        # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
python -m scripts.serve_demo                            # → http://localhost:8000
pytest -q                                               # 23 tests
```

> The offline mode shows the **plumbing**: ingestion, hybrid retrieval, the agent graph, citations, abstention,
> tracing, cache, eval. Answer quality comes from a real model; offline "answers" are extracted sentences.

## 2. Real run with Gemini

**Fastest path, Gemini API key** (no GCP project needed):

```bash
set GEMINI_API_KEY=...            # macOS/Linux: export GEMINI_API_KEY=...
python -m scripts.showcase        # ingest → search → 6 agent runs → full eval + LLM judge → docs/RESULTS.md
```

**Production path: Vertex AI + Atlas**

```bash
copy .env.example .env            # MONGODB_URI (Atlas), GOOGLE_CLOUD_PROJECT (switches the client to Vertex AI)
gcloud auth application-default login
python -m scripts.setup_db        # vector + text search indexes, TTLs; waits until queryable
python -m scripts.ingest data/sample --engagement acme-payments
uvicorn verity.api.main:app       # UI at http://localhost:8000, API docs at /docs
python -m scripts.ask "What are the top reliability risks of the payments platform?" --trace
python -m eval.run_eval --agent --judge --save-baseline
python -m scripts.load_test --concurrency 4 --requests 20 --no-cache
```

**Models & resilience.** `MODEL_FAST` (supervisor, ReAct, verifier) and `MODEL_STRONG` (analyst, writer, judge)
are configurable; defaults are `gemini-3.8-flash` / `gemini-3.1-pro-preview`. Inner steps use Gemini 3 `thinking_level=low`.
The gateway honours 429 `retryDelay`, puts rate-limited models on a cooldown (circuit breaker), and walks the
`MODEL_FALLBACKS` chain on quota or overload errors. Each span records which model actually answered, and cost
uses the price table in `config.py`, which counts thinking tokens as output.

Deploy: `PROJECT=my-gcp-project ./deploy/deploy.sh`. It sets up Cloud Run, Secret Manager, the GCS bucket, the
Eventarc trigger and a backfill Job. After that, dropping a file at `gs://<bucket>/<engagement>/file.md` ingests
it automatically.

## 3. Demo script

| # | Do | Show |
|---|---|---|
| 1 | **Sources** → import files (or point at `data/sample`) | versions, chunk counts, redaction; click a source for its original text |
| 2 | **Search** `ERR-4471` in *keyword* vs *vector* vs *hybrid* | exact codes need the keyword leg; RRF ranks per leg |
| 3 | **Ask** "Is the payments platform really able to survive a regional outage?" | multi-source evidence (architecture §4, July postmortem, 2022 runbook, incident CSV); plan → ReAct steps → verifier |
| 4 | Click any `[S#]` | drawer with the exact passage **highlighted inside the original document** (char offsets), source version and date |
| 5 | Conflicts box | "multi-region active-active" (architecture doc) vs single-region data tier (postmortem) |
| 6 | Ask "What was the result of the SOC 2 Type II audit?" | `insufficient evidence` + Discovery Pack, zero citations |
| 7 | **Memory** → client statement "DR failover test passed 2024-10-14", then ask again | brief changes, statement labelled unverified; cache invalidated automatically |
| 8 | **Runs & metrics** | p50/p95, cost/request, tokens/sec, per-node breakdown; click a run to replay it |
| 9 | **Evaluation** → run with *full agent* + *LLM judge* | recall/MRR/nDCG per mode, abstention, citation precision, faithfulness, history |

## 4. API

| Method | Path | Purpose |
|---|---|---|
| POST | `/ingest` | multipart upload (md/txt/html/pdf/csv/json), versioned |
| POST | `/events/gcs` | Eventarc target for GCS object finalize (Cloud Run IAM) |
| GET | `/engagements`, `/sources`, `/sources/{id}`, `/chunks/{id}` | browse corpus & exact passages |
| POST | `/search` | `vector` / `text` / `hybrid` with metadata filters |
| POST | `/ask` | agent run → brief, citations, conflicts, discovery, plan, tool log, verification, spans, metrics |
| GET | `/runs`, `/runs/{id}`, `/metrics` | traces and LLM-native metrics |
| POST | `/eval`, GET `/eval/history` | golden-set evaluation |
| POST/GET/DELETE | `/feedback`, `/memories`, `/memories/{id}` | engagement memory |

All endpoints except `/health`, `/` and `/events/gcs` require `X-API-Key` when `VERITY_API_KEY` is set.

## 5. Layout

```
verity/   config · db · embeddings · security · service
          llm/gateway.py            gemini | claude | fake, usage capture
          ingest/                   loaders · chunker · pipeline · gcs
          retrieval/                hybrid (RRF + fallbacks + IDF helpers) · structured (safe aggregations)
          agents/                   state · prompts · nodes · graph (LangGraph + MongoDBSaver)
          memory/store.py           engagement memory
          observability/tracing.py  runs/spans, cost, tokens/sec, OpenTelemetry
          api/main.py + static/     REST API + single-page UI
eval/     golden.jsonl · metrics · judge (LLM-as-judge) · run_eval (baseline + gate) · baseline.json
scripts/  serve_demo · showcase · setup_db · ingest · ask · explain_query · load_test
deploy/   deploy.sh (Cloud Run, Secret Manager, GCS, Eventarc, Job)   .github/workflows/ci.yml
docs/     RESULTS.md (real run) · DISCOVERY.md · FIELD_NOTES.md
```

## 6. Results

### Real run with Gemini (2026-11-20; full report in [docs/RESULTS.md](docs/RESULTS.md))

Gemini API free tier · `gemini-embedding-001` (768-d) · agents answered by `gemini-3.5-flash-lite` after fallback ·
in-process MongoDB with the Python cosine/BM25 retrieval fallbacks (no Atlas cluster was available).

| retrieval (12 golden Qs, k=5) | recall@5 | MRR | nDCG@5 |
|---|---|---|---|
| vector (Gemini embeddings) | 1.00 | 0.93 | 0.95 |
| keyword (BM25) | 0.90 | 0.80 | 0.83 |
| hybrid (RRF) | 1.00 | 0.93 | 0.94 |

| agent | abstention accuracy | citation precision | faithfulness (LLM judge) | mean cost / question |
|---|---|---|---|---|
| full graph | **1.00** (12/12; both unanswerable Qs refused) | 0.84 | 0.88 | $0.006 |

Latency (p50 56 s, p95 16 min) is dominated by free-tier rate-limit waits, not by the system. The conflict between
the architecture doc and the July postmortem was flagged automatically. Re-run with `python -m scripts.showcase`.

### Offline baseline (`eval/baseline.json`; fake LLM + hash embeddings, used by the CI gate)

| mode | recall@5 | MRR | nDCG@5 |
|---|---|---|---|
| vector | 0.90 | 0.67 | 0.70 |
| keyword (BM25) | 0.90 | 0.80 | 0.83 |
| **hybrid (RRF)** | **1.00** | 0.72 | 0.78 |

The offline numbers only show that the harness and the gate work; quality figures come from the real run above.
