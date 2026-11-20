---
title: Postmortem INC-1182 PAY-API timeouts (ERR-4471)
kind: postmortem
owner: acme-sre
doc_date: 2024-03-18
---
# Postmortem INC-1182: PAY-API timeouts with ERR-4471

## Summary

On 2024-03-15 between 18:02 and 18:49 UTC, 31% of partner payment requests to PAY-API failed with error code
ERR-4471 (upstream timeout). Severity: SEV1. Duration 47 minutes. Estimated failed payment volume: 212,000.

## Root Cause

A spring sale traffic spike exhausted Redis connections. PAY-API pods opened a new Redis connection per request
under retry storms, hitting the Redis maxclients limit of 10,000. Requests then blocked on the idempotency-key
lookup until the gateway timeout fired and returned ERR-4471.

## What Went Wrong

- Restarting PAY-API pods made the incident worse: each restart triggered a reconnect storm.
- There was no alert on Redis connected_clients; responders found the cause after 30 minutes.
- Client retries had no jitter.

## Remediation

1. Introduce a bounded Redis connection pool (max 200 per pod) - DONE.
2. Alert when Redis connected_clients exceeds 80% of maxclients - DONE.
3. Add exponential backoff with jitter to partner SDKs - IN PROGRESS, owner: partner-integrations.
