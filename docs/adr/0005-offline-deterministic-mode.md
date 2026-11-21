# ADR 0005: Deterministic offline mode for tests and the CI eval gate

Status: accepted

## Context
Real-model evaluation costs money, is rate limited and is non-deterministic, so it cannot gate every PR.

## Decision
`LLM_PROVIDER=fake`, `EMBEDDING_PROVIDER=hash` and `MONGODB_URI=mongomock://` run the full graph with
heuristic stand-ins. CI gates on the offline baseline (catches plumbing and retrieval regressions); real-model
evaluation runs on demand and is resumable with a persistent embedding cache.

## Consequences
The offline gate measures the harness, not answer quality; reports always label which mode produced a number.
Production refuses to start with offline providers (`ENV=production` validation).
