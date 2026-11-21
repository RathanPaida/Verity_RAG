# ADR 0003: Hybrid retrieval fused with Reciprocal Rank Fusion

Status: accepted

## Context
Questions mix exact identifiers (`ERR-4471`, `PAY-API`, SEV levels) with paraphrased concepts ("can it survive
a regional outage?").

## Decision
Run vector and keyword search in parallel and fuse ranks with RRF (k=60, per-leg weights configurable). RRF
needs no score calibration between legs.

## Evidence
Ablation on the 46 answerable golden questions with Gemini embeddings (docs/ABLATIONS.md): vector recall@5
0.98 [0.95–1.00], keyword 0.90 [0.81–0.97], hybrid 0.98 [0.95–1.00]. Hybrid matches vector on this corpus
and protects identifier-heavy queries where the keyword leg ranks higher; weights 1.0/0.5 gave the best MRR.

## Consequences
Two index round-trips per query. The ablation is re-run whenever chunking, embeddings or weights change.
