# Discovery and problem framing

## Problem
Engineering due diligence (M&A, vendor risk, platform audits) means reading dozens of architecture docs,
postmortems, security reviews and incident exports under time pressure. Analysts miss contradictions
between documents ("multi-region" on paper, single-region in the incident report). Findings often can't be traced
back to evidence, and unknowns get papered over instead of raised with the client.

## User
A due-diligence / solutions engineer embedded with a client for 1–4 weeks. Needs: fast answers with
evidence, a defensible risk register, and a list of what to ask in the next workshop.

## Jobs to be done
1. Import the client's material (files or a GCS drop folder) and make it searchable within minutes.
2. Ask cross-cutting questions whose answers span several documents and structured records.
3. Get a brief where every finding links to the exact passage; see where sources disagree.
4. When evidence is missing, get discovery questions instead of a confident guess.
5. Record client statements and analyst preferences so the next brief reflects them.

## Success metrics (gated in CI via `eval/run_eval.py --gate`)
| Metric | Target (real models) | Where measured |
|---|---|---|
| Retrieval recall@5 (hybrid) | ≥ 0.85 | `eval_runs.summary["hybrid.recall"]` |
| Citation precision | ≥ 0.90 | agent eval |
| Faithfulness (LLM judge) | ≥ 0.90 | `eval/judge.py` |
| Abstention accuracy on unanswerable questions | 100% | agent eval |
| p95 latency per brief | < 25 s | `/metrics`, load test |
| Cost per request | < $0.05 | `/metrics` (`runs.totals.cost_usd`) |

## Constraints & risks raised with the client
- Data residency: Atlas + Vertex AI in the same GCP region; no data leaves the project.
- Confidentiality: per-engagement isolation in every query; secrets/PII redacted before embedding.
- Customer docs may contain instructions: evidence is delimited and treated as data; tools are read-only.

## Discovery questions template (ask in workshop 1)
- Which systems are in scope, and who owns each? Where is the latest architecture doc?
- Export of incidents for the last 12–24 months (CSV: id, date, service, severity, minutes_down, cause)?
- Most recent security review / pen test / compliance reports (SOC 2, ISO 27001) and their dates?
- When was DR last tested end-to-end, and is there a written result?
- Which documents are known to be outdated?
