---
title: On-call Handbook
kind: runbook
owner: acme-sre
doc_date: 2024-08-20
---
# On-call Handbook: Payments

## Severity definitions

- SEV1: full outage of payments or data loss risk. Page primary and secondary immediately.
- SEV2: partial outage or more than 5% failed payments. Page primary.
- SEV3: degraded non-critical function. Handle in business hours.

## Rotation

Five engineers rotate weekly. In Q3 2024 the primary on-call received an average of 38 pages per week, of which
61% were non-actionable alerts. Two engineers left the rotation in September citing burnout.

## Escalation

If the primary does not acknowledge within 15 minutes, the page escalates to the secondary, then to the
engineering manager after 30 minutes.
