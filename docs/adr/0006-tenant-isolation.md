# ADR 0006: Tenant isolation by engagement-scoped IDs and mandatory filters

Status: accepted

## Context
The ablation harness found that ingesting the same file name for two clients collided on a global source ID.

## Decision
Source and chunk IDs are namespaced (`{engagement}:{source_key}@v{n}#{i}`). Every retrieval, aggregation,
memory and cache query carries `engagement_id` as a pre-filter; the structured-record tool builds its pipeline
from a whitelisted spec, never raw MQL.

## Verification
The golden set includes cross-tenant questions whose answers exist only in a second client's corpus; the eval
counts leaked passages (0 across all configurations) and a regression test ingests the same file into two
engagements.
