# ADR 0004: Provider-agnostic model gateway with fallback chain and circuit breaker

Status: accepted

## Context
During development the strong-tier preview model had a zero free quota, newer Flash models returned 503/504
for minutes, and stacked SDK + application retries pushed one question to 9 minutes (docs/FIELD_NOTES.md).

## Decision
All model calls go through `llm/gateway.py`: configurable fast/strong tiers, an ordered `MODEL_FALLBACKS`
chain, 429 handled once in the gateway (server `retryDelay` sets a per-model cooldown, i.e. a circuit
breaker), 5xx and network errors fall through to the next model, 4xx client errors fail fast. Every span
records the model that actually answered and its cost, with thinking tokens billed as output.

## Consequences
Answer quality can silently shift to a weaker model under load, so the fallback rate is a log-based metric
with its own alert, and the eval reports which models served each run.
