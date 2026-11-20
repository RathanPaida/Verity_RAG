# Field notes → feature requests

Friction found while building and running Verity against real models, written as product feedback (the
"critical feedback loop" part of a forward-deployed role). Each row says what changed in the code.

| Date | Area | Observation | Impact | Fix in Verity / proposed feature |
|---|---|---|---|---|
| 2026-11-20 | Models | `gemini-2.5-flash` returns 404 "no longer available to new users" on a new Gemini API key | Hard-coded model IDs break new customers on day 1 | Model IDs are config (`MODEL_FAST`/`MODEL_STRONG`) plus a `MODEL_FALLBACKS` chain. **Request:** a stable model alias with a deprecation window per tier |
| 2026-11-20 | Quotas | `gemini-3.1-pro-preview` free tier: `limit: 0` (429 on the first call) | The strong tier is unusable without billing | The gateway falls back to the next model on 429 and records which model actually answered in every span |
| 2026-11-20 | Capacity | Newest Flash models returned 503 "high demand" / 504 for minutes at a time | A single-model agent stalls for 150 s+ per call | Circuit breaker: a 429/503 puts the model on cooldown (server `retryDelay`) and later calls go straight to the fallback |
| 2026-11-20 | Rate limits | Free-tier per-minute limits trip after a few agent calls; SDK retries + our retries stacked to 9 min for one question | Very high latency | 429 removed from SDK retry codes, handled once in the gateway with `retryDelay` + fallback |
| 2026-11-20 | Output format | The model writes grouped citations `[S7, S8, S9]` despite the prompt showing `[S3]` | 8/9 citations were silently dropped by a single-label parser | `normalize_citations()` accepts groups, validates labels against the evidence set, drops invented ones (unit-tested) |
| 2026-11-20 | Agent loop | ReAct researcher re-ran `expand_context` on chunks already collected | Wasted tool calls and tokens | Tool observation now says "already in evidence, search a different angle or finish" |
| 2026-11-20 | Infra | Local Atlas (`mongodb/mongodb-atlas-local`) needs Docker with WSL2; not available on the build machine | No local `$vectorSearch` | Python cosine/BM25 fallbacks behind the same interface; `mongomock://` mode. **Request:** a Windows-native local Atlas Search option |
| | Retrieval | Exact incident codes (ERR-4471) rank low with vector-only search | Lower recall on code/ID questions | Hybrid RRF as the reusable `retrieval/hybrid.py` module |
