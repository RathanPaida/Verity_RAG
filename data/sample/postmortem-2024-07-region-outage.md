---
title: Postmortem INC-1247 us-east1 regional outage
kind: postmortem
owner: acme-sre
doc_date: 2024-07-22
---
# Postmortem INC-1247: Full payments outage during us-east1 incident

## Summary

On 2024-07-19 a Google Cloud networking incident degraded us-east1 for 4 hours. The payments platform was
completely unavailable for 3 hours 12 minutes. Severity: SEV1.

## Timeline

- 09:14 UTC us-east1 network degradation begins; ledger-service writes start failing.
- 09:20 Global load balancer shifts PAY-API traffic to us-west1 as designed.
- 09:21 us-west1 PAY-API returns errors: ledger-service in us-west1 cannot reach the ledger-db primary, which
  only exists in us-east1. There is no cross-region replica of ledger-db.
- 10:05 Team attempts the manual ledger-db failover runbook; the runbook references a decommissioned
  replica host and fails.
- 12:26 us-east1 recovers; payments resume.

## Root Cause

The platform is effectively single-region for its data tier. Stateless services run in both regions, but
ledger-db has a single primary and both read replicas in us-east1. The managed failover controller only
fails over within a region.

## Contributing Factors

- The disaster-recovery runbook had not been tested since 2022.
- The architecture documentation describes the platform as multi-region active-active, which led the
  incident commander to wait for automatic recovery for 45 minutes.

## Action Items

1. Create a cross-region replica of ledger-db in us-west1 - NOT STARTED (blocked on budget approval).
2. Quarterly DR failover game days - NOT STARTED.
3. Correct the architecture documentation - NOT STARTED.
