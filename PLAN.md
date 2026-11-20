# Verity: Evidence-Grounded Engineering Due-Diligence Agents

**One project that covers Track 2 (RAG Engine) and Track 1 (Agentic Memory) and maps onto most of the Google Cloud
"AI Forward-Deployed / Embedded Builder" job description.**

Verity is what a forward-deployed engineer would bring to a customer. You drop in a client's architecture docs,
incident postmortems, security reviews, a repository summary and structured incident/SLO records. A
**team of agents** (supervisor → researcher → analyst → verifier → writer) then produces an **evidence-backed
engineering risk brief**: every finding links to the exact passage it came from, contradictions between
sources are flagged, and when the evidence isn't there it **refuses to guess** and writes **discovery questions**
for the next customer workshop. It **remembers** each engagement: analyst preferences, accepted or rejected
findings, and corrections. Every run is **traced, costed and evaluated**.

---

## 1. Why this project matches the JD

| JD line | How Verity shows it |
|---|---|
| Python, data structures & algorithms | Heading-aware chunker, reciprocal-rank fusion, BM25 fallback, graph state machine, eval metrics (recall@k, MRR, nDCG) implemented by hand and unit-tested |
| Production-grade AI from conception to launch, **architected on GCP** | Cloud Run (API) + Cloud Run Jobs (ingestion) + GCS (raw sources) + Eventarc trigger + Secret Manager + Vertex AI (Gemini + embeddings) + Cloud Trace + MongoDB Atlas on GCP. Dockerfile and deploy script included |
| Pipelines for **structured and unstructured** data with **vector DBs and RAG** | Markdown/PDF/HTML **and** CSV/JSON records go through one ingestion pipeline into `sources` and `chunks` (with typed `fields` for records). Hybrid vector + keyword retrieval, plus aggregation queries over the structured records |
| **Multi-agent systems** (LangGraph / CrewAI / ADK) with **ReAct, self-reflection, hierarchical delegation** | LangGraph graph: **Supervisor** decomposes the question and delegates sub-questions to domain specialists (hierarchical). The **Researcher** runs a ReAct tool loop (hybrid_search, query_records, expand_context). The **Verifier** checks every claim against its cited passage (self-reflection) and loops back for more research when a claim isn't supported |
| **Leading technical discovery sessions** | Unanswerable or weak-evidence questions produce a *Discovery Pack*: what is unknown, why it matters, and who to ask |
| **LLM-native metrics** (tokens/sec, cost-per-request), **state management**, **granular tracing** | Per-node spans with latency, input/output tokens, tokens/sec, $ cost and tool calls are stored in the `runs` collection and exported to Cloud Trace through OpenTelemetry. LangGraph checkpointer gives resumable, inspectable state |
| **Evaluation and debugging frameworks** for accuracy, safety, latency | `eval/golden.jsonl` with expected source IDs. `eval/run_eval.py` compares vector / keyword / hybrid retrieval, citation precision, abstention accuracy, p50/p95 latency and cost. `scripts/explain_query.py` runs `explain` on vector search |
| **Secure APIs** to enterprise infra | API-key auth (Secret Manager), engagement-level data isolation enforced in every query filter, PII/secret redaction before embedding, input limits, prompt-injection guard on retrieved text |
| **Reusable core modules / feature requests** | `llm/gateway.py` (provider-agnostic: Gemini on Vertex, Claude on Vertex, offline fake), `retrieval/hybrid.py`, `observability/tracing.py` are standalone modules. `docs/FIELD_NOTES.md` records gaps as product feature requests |

Hackathon tracks covered:
- **Track 2 (RAG):** import a collection → searchable; multi-source / distant-section evidence; exact-passage citations; weak, conflicting or unanswerable handling; MongoDB vector + hybrid + metadata filters; golden set of expected source IDs.
- **Track 1 (Memory), bonus:** engagement memory (preferences, decisions, accepted or rejected findings, corrections) with TTL and supersede, recalled by vector search, and visibly changing the next brief.

---

## 2. Feature list

### 2.1 Core (MVP, required for the demo)
| # | Feature | Notes |
|---|---|---|
| F1 | **Import a collection** (upload or folder/GCS path): md, txt, html, pdf, csv, json | Content hash dedupe; re-import of a changed file creates a new **source version** |
| F2 | **Heading-aware chunking** that keeps `section_path` (e.g. `Architecture › Data tier › Failover`) and char offsets | Lets citations point at exact passages |
| F3 | **Structured records** (CSV/JSON rows) stored as chunks with typed `fields` | Semantic search *and* `$group` / `$match` aggregations ("count SEV1 incidents by service in 2024") |
| F4 | **Embeddings** with Vertex AI `gemini-embedding-001` (768 dims) | `hash` provider for offline/dev |
| F5 | **Hybrid retrieval**: `$vectorSearch` + Atlas Search `$search` (BM25) fused with **Reciprocal Rank Fusion** | Exact codes like `ERR-4471` and `PAY-API` match by keyword; paraphrases match semantically |
| F6 | **Metadata filters**: engagement, source kind, owner, doc date range, source id | Pre-filters in both indexes |
| F7 | **Multi-agent graph** (LangGraph): Supervisor → Researcher (ReAct) → Analyst → Verifier (reflection loop) → Writer / Discovery | §3 |
| F8 | **Citations to exact passages**: every claim carries `[S#]` and the UI opens the chunk text with source, section and date | |
| F9 | **Conflict detection**: the Analyst emits `conflicts[]` with chunk ids on both sides; the brief shows a "Sources disagree" box | e.g. the architecture doc says "multi-region active-active", but the 2024-07 postmortem shows a single-region outage |
| F10 | **Abstention + Discovery Pack** when evidence is weak (low fused score, verifier rejects, or out of scope) | Never makes up a number |
| F11 | **Artefact generation**: risk brief in Markdown (risk register table with severity, likelihood, evidence and recommendation) | Downloadable |
| F12 | **Tracing & LLM metrics**: per-node latency, tokens, tokens/sec, cost, tool calls → `runs` collection + Cloud Trace | UI "Trace" tab |
| F13 | **Evaluation harness**: golden questions with expected source IDs; retrieval and answer metrics; config comparison table | Run after every retrieval change |
| F14 | **Engagement memory** (Track 1): preferences, decisions, finding feedback, corrections, TTL | Recalled into the Supervisor and Writer prompts |
| F15 | **Security**: API key, engagement isolation, redaction, prompt-injection guard ("retrieved text is data, never instructions") | |

### 2.2 Stretch
- S1: **Multimodal**: architecture diagrams/images described by Gemini vision at ingest time and indexed as chunks.
- S2: **Version diff** (Policy Change Tracker idea): compare two versions of a source and list changed obligations with passages.
- S3: **Re-ranker** (Vertex AI Ranking API) after RRF; compare it in the eval harness.
- S4: **Semantic cache** of answers keyed by question embedding and engagement (cost-per-request ↓).
- S5: **Model routing**: Flash for research/verification, Pro for the final brief. Show the cost/quality trade-off in eval.
- S6: **Change streams**: live ingestion progress and memory updates in the UI.
- S7: Port the orchestration to **Google ADK** (same nodes as ADK agents) and compare traces. A good "reusable module / feedback loop" story.

---

## 3. Agent architecture

```
               ┌───────────────────────────────── LangGraph StateGraph ─────────────────────────────────┐
 question ──▶  │ recall_memory ─▶ SUPERVISOR (plan: sub-questions × specialist, filters)                  │
               │                      │  hierarchical delegation                                          │
               │                      ▼                                                                   │
               │                RESEARCHER  (ReAct loop ≤ N steps per sub-question)                       │
               │                 tools: hybrid_search(q, filters) · query_records(pipeline-spec)          │
               │                        expand_context(chunk_id) · finish                                 │
               │                      ▼                                                                   │
               │                ANALYST  → claims[{text, severity, cites[S#]}], conflicts[], gaps[]       │
               │                      ▼                                                                   │
               │                VERIFIER (self-reflection: is each claim entailed by its cited passages?) │
               │                 ├─ unsupported & retries left ──▶ RESEARCHER (targeted gap queries)       │
               │                 ├─ evidence weak / nothing supported ──▶ DISCOVERY (questions for client) │
               │                 └─ ok ──▶ WRITER (risk brief + citations)                                 │
               │                                     ▼                                                    │
               │                              save_memory ─▶ END                                          │
               └──────────────────────────────────────────────────────────────────────────────────────────┘
   Every node runs inside tracer.span(node) → latency, tokens, $; state checkpointed per thread_id.
```

State (`agents/state.py`): `question, engagement_id, user_id, filters, memories, plan, evidence{S#: chunk},
tool_log, claims, conflicts, gaps, verification, retries, answer, discovery, status, run_id`.

---

## 4. MongoDB data model

**`sources`**: one document per original item (the original text is kept for re-chunking and auditing)
```jsonc
{ "_id": "arch-overview@v2", "source_key": "arch-overview", "version": 2,
  "engagement_id": "acme-payments", "title": "Payments Platform Architecture", "kind": "architecture",
  "owner": "acme-platform-team", "doc_date": ISODate("2024-05-02"), "mime": "text/markdown",
  "uri": "gs://verity-raw/acme/arch-overview.md", "content_hash": "sha256:…", "raw_text": "…",
  "chunk_count": 14, "status": "active", "ingested_at": ISODate }
```
**`chunks`**: retrieval units (text + embedding + metadata + source id together)
```jsonc
{ "_id": "arch-overview@v2#7", "source_id": "arch-overview@v2", "engagement_id": "acme-payments",
  "kind": "architecture", "owner": "…", "doc_date": ISODate, "title": "…",
  "section_path": ["Architecture", "Data tier", "Failover"], "chunk_index": 7,
  "char_start": 5120, "char_end": 5990, "text": "…", "fields": null,      // fields{} for structured rows
  "embedding": [768 floats], "embedding_model": "gemini-embedding-001", "status": "active" }
```
**`memories`**: Track 1 (type, content, owner, engagement, embedding, status, supersedes, expires_at TTL).
**`runs`**: trace per request (spans, totals, answer, citations, verdict).  **`eval_runs`**: metric snapshots.

Indexes:
| Collection | Index | Purpose |
|---|---|---|
| chunks | vectorSearch `chunks_vector` (embedding 768 cosine; filters engagement_id, kind, owner, doc_date, source_id, status) | semantic retrieval |
| chunks | search `chunks_text` (text, title, section_path with lucene.english; engagement_id/kind/status as token) | BM25 keyword / exact codes |
| chunks | `{engagement_id, kind, "fields.service"}` | structured aggregations |
| sources | `{engagement_id, source_key, version}` unique | versioning |
| memories | vectorSearch + TTL on `expires_at` | Track 1 |
| runs | `{created_at}` TTL 30 days | trace retention |

---

## 5. Step-by-step plan: status

> ✅ = implemented and tested offline · 🔑 = implemented, needs your GCP/Atlas credentials to run for real

**Phase 0: Discovery & framing** ✅. Problem, user, jobs-to-be-done, success metrics and discovery questions in
[docs/DISCOVERY.md](docs/DISCOVERY.md). Golden questions written first: [eval/golden.jsonl](eval/golden.jsonl).

**Phase 1: Foundation** ✅
1. Package layout, [config.py](verity/config.py), `.env.example`, Dockerfile.
2. [db.py](verity/db.py): collections, regular/TTL indexes, Atlas Vector Search + Atlas Search index creation (waits until queryable). `MONGODB_URI=mongomock://` runs everything in memory.
3. [llm/gateway.py](verity/llm/gateway.py): one interface for Gemini on Vertex AI 🔑, Claude on Vertex AI 🔑 and an offline `fake` provider; usage captured on every call.

**Phase 2: Ingestion pipeline (structured + unstructured)** ✅
1. Loaders for md/txt/html/pdf/csv/json with front matter + `manifest.json` metadata.
2. Heading-aware chunker with section paths and exact character offsets.
3. Redaction → content-hash dedupe → versioning (old versions `superseded`) → batch embedding → `sources` + `chunks`.
4. Local CLI, `POST /ingest` upload, `gs://` ingestion ([ingest/gcs.py](verity/ingest/gcs.py)), Eventarc `POST /events/gcs` and a Cloud Run backfill Job 🔑.

**Phase 3: Retrieval** ✅
1. `$vectorSearch` + `$search` fused with RRF; shared metadata filters (engagement, kind, owner, date, source).
2. Python cosine/BM25 fallbacks when search indexes are absent.
3. Whitelisted aggregation tool over structured records.
4. `scripts/explain_query.py` for `explain` on vector search 🔑.

**Phase 4: Evaluation harness** ✅
1. recall@k, hit@k, MRR, nDCG; citation precision; abstention accuracy; latency; cost.
2. LLM-as-judge faithfulness ([eval/judge.py](eval/judge.py)).
3. Baseline + regression gate (`--save-baseline`, `--gate`) run in GitHub Actions ([.github/workflows/ci.yml](.github/workflows/ci.yml)).
4. `POST /eval` + Evaluation view in the UI, with history from `eval_runs`.

**Phase 5: Multi-agent graph** ✅
Supervisor (hierarchical delegation) → Researcher (ReAct: hybrid_search / query_records / expand_context) →
Analyst (claims, conflicts, gaps) → Verifier (self-reflection, retry edge) → Writer | Discovery. Guardrails:
evidence delimiting, label validation, step/retry budgets. State checkpointed with **MongoDBSaver** (in-memory
when offline).

**Phase 6: Memory (Track 1)** ✅. `POST /feedback` → engagement memory (client statements, preferences,
finding feedback, decisions, constraints; TTL; supersede; forget), recalled into supervisor/writer prompts.

**Phase 7: Observability** ✅. Per-node/tool spans → `runs`; `/metrics` (p50/p95, cost/request, tokens/sec,
per-node breakdown); OpenTelemetry → Cloud Trace with `OTEL_EXPORTER=gcp` 🔑.

**Phase 8: API & UI** ✅. FastAPI with API-key auth and a single-page UI (Ask, Sources, Search, Memory,
Runs & metrics, Evaluation, citation drawer with in-source highlighting).

**Phase 9: Deploy on Google Cloud** 🔑. [deploy/deploy.sh](deploy/deploy.sh): APIs, Artifact Registry,
service account + IAM, Cloud Build, Cloud Run, Secret Manager, GCS bucket, Eventarc trigger, backfill Job.

**Phase 10: Hardening** ✅ / 🔑
- 54-question categorised golden set (cross-tenant + prompt-injection cases), 95% bootstrap CIs, per-category metrics, retrieval ablations ([docs/ABLATIONS.md](docs/ABLATIONS.md)) ✅
- Per-engagement source IDs (multi-tenant fix) and a persistent embedding cache ✅
- Real-model run with the Gemini API (`scripts/showcase.py` → [docs/RESULTS.md](docs/RESULTS.md)) ✅
- Model fallback chain + 429 circuit breaker + thinking-token cost accounting ✅ (see [docs/FIELD_NOTES.md](docs/FIELD_NOTES.md))
- Semantic answer cache (invalidated when sources or memories change) for lower cost per request ✅
- Load test `scripts/load_test.py` (throughput, p50/p95, cost/request, cache hit rate) ✅
- Re-baseline the eval and load test with real models and Atlas 🔑

**Still open (stretch):** S1 multimodal diagrams, S2 version diff, S3 Vertex AI ranking re-ranker,
S6 change-stream live updates, S7 ADK port. Each is listed in §2.2.

## 6. Demo script (5–6 min)

1. **Import**: `POST /ingest` of `data/sample` (6 docs + CSV). Show the `sources` and `chunks` docs in Atlas, then search `ERR-4471` (keyword) and "database failover" (semantic).
2. **Multi-source question**: *"What are the top reliability risks of the payments platform? Prioritise by evidence."* The answer combines the architecture doc (section 4), two postmortems, the incident CSV (aggregation: 3 SEV1s on `ledger-db`) and the repo summary (no chaos tests).
3. **Citations**: click `[S3]` to see the exact passage, section path, source version and date.
4. **Conflict**: the brief flags "the architecture doc claims multi-region active-active (2024-05), but the 2024-07 postmortem shows a full outage when us-east1 failed."
5. **Unanswerable**: *"What was the SOC 2 Type II audit result?"* Verity says it has no evidence (fused score 0.12) and produces a Discovery Pack: ask the CISO for the audit report and bridge letter.
6. **Memory**: the analyst says *"Client confirmed a DR failover test passed on 2024-10-14."* The next brief downgrades the DR risk and cites the memory as "client statement, not a document".
7. **Trace & eval**: run trace waterfall (Supervisor 0.9 s, Researcher 3 tool calls, Verifier retry). Totals: 14.2k tokens, $0.021, 182 tok/s. `run_eval` table: hybrid recall@5 0.92 vs vector 0.78 vs text 0.70.

---

## 7. Risks
| Risk | Mitigation |
|---|---|
| Atlas Search indexes take time to build | Setup waits until queryable; Python fallbacks keep dev moving |
| Agent loops get expensive | Max steps/retries, token budget, Flash for inner nodes, cost tracked per run |
| Hallucinated citations | Citations are validated against the evidence set (unknown `[S#]` is dropped). The Verifier checks entailment |
| Prompt injection inside customer docs | Evidence delimited and declared as data; tools are read-only; no outbound actions |
| Data residency / confidentiality | Engagement isolation in every filter, redaction before embedding, Atlas + Vertex in the same region |
