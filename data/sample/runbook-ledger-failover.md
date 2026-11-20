---
title: Runbook ledger-db manual failover
kind: runbook
owner: acme-dba
doc_date: 2022-11-08
---
# Runbook: ledger-db manual failover

## When to use

Use only if the managed failover controller does not promote a replica within 5 minutes.

## Steps

1. Freeze writes by scaling ledger-service to zero replicas.
2. Promote replica host ledger-db-replica-2.us-east1 using the promote command.
3. Update the ledger-db DNS record to the promoted host.
4. Scale ledger-service back up and verify replication lag is zero.

## Last tested

This runbook was last exercised on 2022-11-08 in staging.
