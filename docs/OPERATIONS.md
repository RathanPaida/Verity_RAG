# Operations

How Verity runs in production on Google Cloud: service levels, dashboards, alerts, runbooks and routine tasks.
Infrastructure is defined in [infra/terraform](../infra/terraform); rollouts happen through
[.github/workflows/deploy.yml](../.github/workflows/deploy.yml).

## 1. Service level objectives

| SLI | Objective (28-day window) | Source |
|---|---|---|
| Availability: non-5xx share of API requests | ≥ 99.5% | Cloud Run `request_count` by `response_code_class` |
| `/ask` latency, uncached | p95 ≤ 60 s | Cloud Run `request_latencies`; per-node detail in `/metrics` |
| `/search` latency | p95 ≤ 1.5 s | Cloud Run `request_latencies` |
| Grounding: answered briefs whose citations all resolve to retrieved evidence | 100% (enforced in code) | `normalize_citations`, eval `citation_precision` |
| Abstention on out-of-corpus questions | ≥ 0.9 on the golden set | `eval/run_eval.py --agent` |
| Cross-tenant leakage | 0 passages | eval `cross_tenant_leaks` |

Quality SLIs are measured by the evaluation harness on every release candidate. Latency and availability come
from platform metrics. A breached quality SLI blocks the release in the same way a failing test does.

## 2. Signals

- **Logs**: JSON in Cloud Logging format. Filter `jsonPayload.request_id="<id>"` to follow one request through
  the access log, gateway warnings and errors. Every response carries `X-Request-ID`, and the bug-report
  template asks for it.
- **Traces**: with `OTEL_EXPORTER=gcp`, every agent node and tool call is a Cloud Trace span carrying
  `llm.tokens_in`, `llm.tokens_out` and `llm.cost_usd`.
- **Run store**: `GET /runs/{id}` returns the full span tree, tool log, verifier verdict and the model that
  served each call, so a run can be replayed for debugging.
- **LLM metrics**: `GET /metrics?engagement_id=…` gives p50/p95 latency, cost per request, tokens/sec, status
  mix and a per-node breakdown.
- **Log-based metric** `verity/llm_fallback_served`: answers served by a fallback model. It is a leading
  indicator of quota exhaustion and quality drift.

## 3. Alerts and runbooks

### 5xx spike
Alert: 5xx ratio above 5% for 10 minutes.
1. Logs Explorer: `severity>=ERROR resource.labels.service_name="verity-api"`, grouped by `request_id`.
2. **502** with "model provider unavailable": every model in `MODEL_FALLBACKS` failed. Check the Vertex AI
   quota page and the provider status. Mitigation: add a model with spare quota to `MODEL_FALLBACKS` (env
   update, no rebuild).
3. **500** "internal error": a code fault. Read the logged traceback for that `request_id`. If it started with
   the latest revision, roll back (§4).
4. **503** at startup: `/readyz` is failing, so MongoDB is unreachable. Check the Atlas status, the IP access
   list or private endpoint, and the `verity-mongodb-uri` secret version.

### Latency
Alert: p95 above 60 s for 15 minutes.
1. `/metrics` per-node breakdown shows whether the time goes to `researcher` (retrieval or tool loops) or to the
   LLM-heavy nodes.
2. Gateway warnings `served by fallback model` together with 429 entries point to rate limiting. Raise quota,
   or set `min_instances=0` and lower `max_instances` to shed load.
3. Slow `hybrid_search` spans point to Atlas. Run `python -m scripts.explain_query` and check the
   `numCandidates` and index status.

### Cost anomaly
Budget alerts fire at 50%, 90% and 100%. `/metrics` `cost_per_request_usd` by engagement finds the source. The
levers, in order:
1. semantic cache TTL (`CACHE_TTL_HOURS`)
2. `MODEL_STRONG` set to a Flash model
3. `MAX_REACT_STEPS` and `MAX_VERIFY_RETRIES`
4. `RATE_LIMIT_PER_MINUTE`

## 4. Deploy and roll back

- **Deploy**: tag `vX.Y.Z`. The workflow builds the image, deploys a revision that receives no traffic, smoke
  tests `/readyz` and `/version` on its tagged URL, then shifts traffic. For a canary, run the workflow
  manually with `traffic=10`.
- **Roll back**: `gcloud run services update-traffic verity-api --to-revisions <previous-revision>=100
  --region <region>`. Revisions are immutable, so a rollback takes seconds and needs no rebuild.
- **Config-only change** (models, budgets, rate limits): `gcloud run services update verity-api
  --update-env-vars KEY=VALUE`. This creates a new revision.

## 5. Routine tasks

| Task | How |
|---|---|
| Rotate the API key | `gcloud secrets versions add verity-api-key --data-file=-`, deploy a new revision, then disable the old version |
| Rotate the MongoDB credentials | Create the new Atlas user, add a secret version, roll a new revision, then delete the old user |
| Change embedding model or dimension | Update the env, run `python -m scripts.setup_db` (rebuilds the vector index), re-ingest, re-run the eval and the ablation |
| Add a golden question | Append to `eval/golden.jsonl` with a category and expected sources. CI picks it up. Re-save the baseline only with a reviewed justification |
| Backfill an engagement | Upload to `gs://<bucket>/<engagement>/` (Eventarc ingests each object), or `gcloud run jobs execute verity-ingest --args=...` |
| Delete a client's data | Delete documents by `engagement_id` in `sources`, `chunks`, `memories`, `runs` and `answer_cache`, then the `gs://<bucket>/<engagement>/` prefix |

## 6. Data retention

- `runs` (traces and answers): 30-day TTL.
- `answer_cache`: TTL from `CACHE_TTL_HOURS`.
- Memories: optional per-item `expires_at`. Soft-deleted memories have their embedding removed.
- Superseded source versions are kept for audit but excluded from retrieval.
- Raw objects in GCS: 5 noncurrent versions.
