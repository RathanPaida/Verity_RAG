# ADR 0001: LangGraph for agent orchestration

Status: accepted

## Context
The workflow is a fixed topology with one loop (verifier → researcher) and per-node budgets. We need resumable
state, inspection of intermediate state for debugging, and per-node tracing.

## Options
1. **LangGraph `StateGraph`**: explicit nodes and conditional edges, pluggable checkpointers (MongoDB).
2. Google ADK: GCP-native, good tool abstractions; hierarchical agents are first class.
3. A single ReAct agent with all tools: least code, least control.

## Decision
LangGraph. The verifier's retry edge and the abstain/discovery branch are conditional edges that are easy to
test deterministically; `MongoDBSaver` keeps checkpoints in the same database as the corpus. Nodes are plain
functions, so an ADK port (roadmap) reuses them unchanged.

## Consequences
Graph topology is code-reviewed like any other code. A single-agent design was rejected because it could not
enforce "verify before write".
