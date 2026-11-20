---
title: Capacity Plan 2025
kind: architecture
owner: acme-platform-team
doc_date: 2024-11-01
---
# Capacity Plan 2025

## Traffic forecast

Black Friday 2024 peak is forecast at 3,600 payment requests per second, 50% above the 2024 spring sale peak.
The last full load test (September 2024) sustained 2,800 requests per second before PAY-API p99 latency exceeded
the 800 ms SLO.

## Data growth

ledger-db stores 6.1 TB of its 10 TB provisioned disk and grows about 18% per month. At the current rate the
volume is projected to be full by August 2025. Storage autoscaling is not enabled.

## Cache

The shared Redis cluster runs at 72% memory utilisation at peak. Eviction policy is noeviction, so writes fail
when memory is full.
