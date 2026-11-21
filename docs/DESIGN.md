# Verity design

Status: implemented (v1.1). Decisions with alternatives considered are recorded in [adr/](adr/).
Operational targets and runbooks: [OPERATIONS.md](OPERATIONS.md).

## 1. Problem and scope

Engineering due diligence (pre-acquisition reviews, vendor risk, platform audits) means reading a client's
architecture docs, postmortems, security reviews, repository summaries and incident exports, then writing a risk
brief whose every finding can be traced to evidence. The hard parts are not summarisation: they are **finding
evidence spread across sources**, **noticing when sources contradict each other**, and **not inventing an answer
when the evidence is missing**. Verity automates the first draft of that brief and makes the gaps explicit as
questions for the next client session. Problem framing and success metrics: [DISCOVERY.md](DISCOVERY.md).

### Capabilities
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
| F14 | **Engagement memory**: preferences, decisions, finding feedback, corrections, TTL | Recalled into the Supervisor and Writer prompts |
| F15 | **Security**: API key, engagement isolation, redaction, prompt-injection guard ("retrieved text is data, never instructions") | |

## 2. Agent architecture

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

## 3. Data model (MongoDB Atlas)

**`sources`**: one document per original item (the original text is kept for re-chunking and auditing)
```jsonc
{ "_id": "acme-payments:arch-overview@v2", "source_key": "arch-overview", "version": 2,
  "engagement_id": "acme-payments", "title": "Payments Platform Architecture", "kind": "architecture",
  "owner": "acme-platform-team", "doc_date": ISODate("2024-05-02"), "mime": "text/markdown",
  "uri": "gs://verity-raw/acme/arch-overview.md", "content_hash": "sha256:…", "raw_text": "…",
  "chunk_count": 14, "status": "active", "ingested_at": ISODate }
```
**`chunks`**: retrieval units (text + embedding + metadata + source id together)
```jsonc
{ "_id": "acme-payments:arch-overview@v2#7", "source_id": "acme-payments:arch-overview@v2", "engagement_id": "acme-payments",
  "kind": "architecture", "owner": "…", "doc_date": ISODate, "title": "…",
  "section_path": ["Architecture", "Data tier", "Failover"], "chunk_index": 7,
  "char_start": 5120, "char_end": 5990, "text": "…", "fields": null,      // fields{} for structured rows
  "embedding": [768 floats], "embedding_model": "gemini-embedding-001", "status": "active" }
```
**`memories`**: engagement memory (type, content, owner, engagement, embedding, status, supersedes, expires_at TTL).
**`runs`**: trace per request (spans, totals, answer, citations, verdict).  **`eval_runs`**: metric snapshots.

Indexes:
| Collection | Index | Purpose |
|---|---|---|
| chunks | vectorSearch `chunks_vector` (embedding 768 cosine; filters engagement_id, kind, owner, doc_date, source_id, status) | semantic retrieval |
| chunks | search `chunks_text` (text, title, section_path with lucene.english; engagement_id/kind/status as token) | BM25 keyword / exact codes |
| chunks | `{engagement_id, kind, "fields.service"}` | structured aggregations |
| sources | `{engagement_id, source_key, version}` unique | versioning |
| memories | vectorSearch + TTL on `expires_at` | engagement memory |
| runs | `{created_at}` TTL 30 days | trace retention |

---

## 4. Request lifecycle

1. `POST /ask` → request-context middleware assigns `X-Request-ID`, binds the Cloud Trace ID, applies the
   per-client rate limit.
2. Semantic answer cache lookup, keyed by question embedding **and** a corpus fingerprint (active sources +
   memories), so any ingestion or memory change invalidates cached answers.
3. LangGraph run inside a traced `Run`; every node and tool call is a span (latency, tokens, cost, model used).
   State is checkpointed per `thread_id` in MongoDB.
4. The LLM gateway serves each call from the primary model, or walks the fallback chain on 429/5xx with a
   per-model circuit breaker that honours the server's `retryDelay`.
5. Citations are validated against the evidence set before the response is returned; the run document is
   persisted to `runs` (TTL 30 days) for replay and metrics.

## 5. Risks and mitigations
| Risk | Mitigation |
|---|---|
| Atlas Search indexes take time to build | Setup waits until queryable; Python fallbacks keep dev moving |
| Agent loops get expensive | Max steps/retries, token budget, Flash for inner nodes, cost tracked per run |
| Hallucinated citations | Citations are validated against the evidence set (unknown `[S#]` is dropped). The Verifier checks entailment |
| Prompt injection inside customer docs | Evidence delimited and declared as data; tools are read-only; no outbound actions |
| Data residency / confidentiality | Engagement isolation in every filter, redaction before embedding, Atlas + Vertex in the same region |

## 6. Roadmap

- **Multimodal**: architecture diagrams/images described by Gemini vision at ingest time and indexed as chunks.
- **Version diff**: compare two versions of a source and list changed obligations with passages.
- **Re-ranker** (Vertex AI Ranking API) after RRF; compare it in the eval harness.
- **Change streams**: live ingestion progress and memory updates in the UI.
- Port the orchestration to **Google ADK** (same nodes as ADK agents) and compare traces.
