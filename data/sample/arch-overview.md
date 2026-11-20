---
title: Payments Platform Architecture Overview
kind: architecture
owner: acme-platform-team
doc_date: 2024-05-02
tags: architecture, payments
---
# Payments Platform Architecture Overview

## 1. Purpose

This document describes the target and current architecture of the Acme payments platform, which processes
card and wallet payments for the Acme storefront and three B2B partners. Peak load is approximately 2,400
payment requests per second during seasonal sales.

## 2. Service Topology

The platform consists of five core services: PAY-API (public payment gateway), checkout-orchestrator,
ledger-service, fraud-scorer and notification-worker. All services run on Kubernetes (GKE) and communicate
over gRPC, except PAY-API which exposes REST to partners.

PAY-API uses a shared Redis cluster for idempotency keys and rate-limit counters. The Redis cluster is
configured with maxclients 10,000.

## 3. Data Tier

ledger-service persists double-entry ledger records in a PostgreSQL 14 cluster called ledger-db. Writes go to a
single primary; two read replicas serve reporting queries. Nightly logical backups are written to Cloud Storage
and retained for 35 days.

fraud-scorer reads features from a BigQuery export refreshed every 15 minutes.

## 4. Availability and Disaster Recovery

The platform is deployed **multi-region active-active** across us-east1 and us-west1. Global load balancing
routes traffic to the healthy region. The stated recovery time objective (RTO) is 15 minutes and the recovery
point objective (RPO) is 1 minute.

Database failover for ledger-db is described as automatic within 60 seconds using the managed failover
controller.

## 5. Observability

Metrics are exported to Cloud Monitoring; logs go to Cloud Logging with 30-day retention. The on-call rotation
has five engineers with a 15-minute paging SLA for SEV1 incidents.
