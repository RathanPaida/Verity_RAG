# ADR 0002: MongoDB Atlas as the single store for vectors, text, records, memory and traces

Status: accepted

## Context
Retrieval needs vector search, BM25 keyword search, metadata pre-filters and aggregations over structured rows.
Every query must be scoped to one client engagement.

## Options
1. **Atlas**: `$vectorSearch` + Atlas Search `$search` + aggregation pipeline in one cluster.
2. Dedicated vector DB (e.g. Vertex AI Vector Search) + separate document store.
3. Postgres + pgvector + full-text.

## Decision
Atlas. One copy of each chunk carries text, embedding, metadata and typed `fields`, so the tenant filter is a
single predicate applied identically in both search legs and in aggregations. Checkpoints, memories, runs and
eval history live in the same cluster with TTL indexes.

## Consequences
Search indexes are eventually consistent; `scripts.setup_db` waits until they are queryable. Offline and CI runs
use Python cosine/BM25 fallbacks behind the same interface (`NoSearchProxy`), which keeps tests hermetic but
means CI does not exercise Atlas ranking. Local parity is available through `docker compose` (atlas-local).
