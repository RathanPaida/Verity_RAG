---
title: SLO Report Q3 2024
kind: slo
owner: acme-sre
doc_date: 2024-10-05
---
# SLO Report Q3 2024: Payments Platform

## Targets and attainment

| Service | Availability SLO | Q3 attainment | Error budget consumed |
|---|---|---|---|
| PAY-API | 99.95% | 99.81% | 380% |
| ledger-service | 99.90% | 99.62% | 380% |
| checkout-orchestrator | 99.90% | 99.97% | 30% |
| fraud-scorer | 99.50% | 99.71% | 58% |

## Commentary

PAY-API and ledger-service exhausted their error budgets in July after INC-1247. Under the error budget policy,
feature launches for both services should have been frozen for the rest of the quarter, but the policy was not
enforced: 14 feature deploys shipped to ledger-service in August and September.

Latency SLO for PAY-API (p99 below 800 ms) was met in 11 of 13 weeks.
