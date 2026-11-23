# Evaluation: 54-question run with Gemini

Run on 2026-11-22 and 2026-11-23. Raw per-question results: [eval/results/agent-54.json](../eval/results/agent-54.json).

## Setup

**Models**
- `gemini-3.5-flash-lite` on the fast tier (supervisor, ReAct, verifier)
- `gemini-3-flash-preview` on the strong tier (analyst, writer, judge)
- Each was also the other's fallback
- Called through the Gemini Developer API on the free tier

**Embeddings:** `gemini-embedding-001`, 768 dimensions.

**Retrieval:** hybrid RRF (k=60, weights 1.0/1.0), top 5. Searches ran on the in-memory store with the Python
cosine and BM25 fallbacks, so Atlas ranking was not exercised.

**Golden set:** 54 questions in 9 categories ([eval/golden.jsonl](../eval/golden.jsonl)). 46 of them have
expected source IDs. The corpus contains:
- 14 sources for the target client
- a second client, used for the cross-tenant traps
- a partner document with a planted prompt injection

**Procedure**
- 49 questions completed in the first pass.
- 5 failed when every model in the chain hit free-tier rate limits (HTTP 429).
- `--resume` re-ran only those 5 the next day. The final run has 54/54 scored and 0 errors.

## Agent results (n = 54, 95% bootstrap CIs)

| Metric | Result |
|---|---|
| Abstention accuracy (answers when it should, abstains when it should) | **0.98** [0.94–1.00] |
| Faithfulness (LLM judge: claims supported by the cited passages) | **0.98** [0.96–0.99] |
| Fact recall (required facts present in the answer) | **0.95** [0.88–1.00] |
| Citation precision (cited passages that come from expected sources) | **0.80** [0.71–0.88] |
| Conflict detection | **4/4** |
| Safety: injected instructions and other-client data never appear in answers | **6/6** checks passed |
| Cross-tenant leakage (retrieval) | **0** passages |
| Mean cost per question (paid-tier list prices, thinking tokens included) | **$0.0078** (run total $0.42; billed $0 on the free tier) |
| Latency p50 / p95 | **29 s / 6.2 min** (the p95 is dominated by free-tier 429 cooldowns) |

## By category

| Category | n | Abstention | Citation precision | Fact recall |
|---|---|---|---|---|
| keyword | 8 | 1.00 | 0.83 | 1.00 |
| semantic | 10 | 1.00 | 0.76 | 1.00 |
| multi_source | 10 | 1.00 | 0.77 | 1.00 |
| structured | 8 | 1.00 | 1.00 | 0.88 |
| conflict | 4 | 1.00 | 0.61 | n/a |
| temporal | 3 | 0.67 | 1.00 | 0.67 |
| unanswerable | 5 | 1.00 | n/a | n/a |
| cross_tenant | 3 | 1.00 | n/a | n/a |
| injection | 3 | 1.00 | 0.47 | 1.00 |

Categories with 3–4 questions are indicative only; their confidence intervals span most of [0, 1].

## Retrieval (46 questions with expected sources, k = 5)

| Mode | recall@5 [95% CI] | MRR [95% CI] | nDCG@5 |
|---|---|---|---|
| vector | 0.98 [0.95–1.00] | 0.95 [0.90–0.99] | 0.95 |
| keyword (BM25) | 0.90 [0.81–0.97] | 0.83 [0.73–0.91] | 0.82 |
| hybrid (RRF) | 0.98 [0.95–1.00] | 0.94 [0.89–0.98] | 0.94 |

## Error analysis

**1. Over-citation is the main weakness (citation precision 0.80).**
Seven answers cite many passages for a claim that one or two support:
- semantic: sem03, sem05, sem07
- multi-source: mul04
- conflict: con03
- injection: inj01, inj02

The facts in these answers are correct (fact recall 1.0, judge faithfulness 0.98). The extra citations are
technically "supported", because they come from retrieved evidence, but they don't point to the expected
source.

Next step: have the writer cite at most 2 passages per claim, and ask the verifier to drop citations that
don't entail the claim. The goal is to reach ≥ 0.9 without losing fact recall.

**2. Temporal: one wrong abstention (tem03, "When does the NorthPay contract renew?").**
- The answer is in `vendor-dependencies` (March 2025).
- Retrieval found it: recall 1.0.
- The supervisor classified the question as out of scope and never researched it.

Next step: add vendor/contract questions to the supervisor's scope examples, and add a regression test for
"answerable but non-technical" questions.

**3. Safety held.**
- The planted injection never changed agent behaviour or appeared in an answer.
- No other-client data appeared.
- All 4 source conflicts were flagged.

**4. Operational.**
- The free tier caused 5/54 transient failures and the 6-minute p95.
- The gateway's fallback chain and cooldowns absorbed most 429s.
- `--resume` meant the 5 failed questions could be re-run without re-running the other 49.

The evaluation also checked two earlier fixes. Both held throughout the run:
- Multi-tenant IDs: 0 leaked passages from the other client.
- Grouped citations: answers citing `[S7, S8]` were scored correctly.

## Reproduce

```bash
MONGODB_URI=mongomock:// CHECKPOINTER=memory LLM_PROVIDER=gemini EMBEDDING_PROVIDER=gemini GEMINI_BACKEND=api \
MODEL_FAST=gemini-3.5-flash-lite MODEL_STRONG=gemini-3-flash-preview \
MODEL_FALLBACKS=gemini-3-flash-preview,gemini-3.5-flash-lite \
python -m eval.run_eval --ingest data/sample --agent --judge --resume --out eval/results/agent-54.json
```
