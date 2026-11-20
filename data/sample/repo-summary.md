---
title: Repository and Delivery Summary
kind: repo
owner: acme-platform-team
doc_date: 2024-10-01
---
# Repository and Delivery Summary (monorepo acme/payments)

## Code Health

- 186k lines of Go and 41k lines of Python across 9 services.
- Unit test line coverage: 41% overall; ledger-service 63%, PAY-API 38%, fraud-scorer 22%.
- No chaos, load or disaster-recovery tests exist in the repository.

## Delivery

- Median CI pipeline duration is 38 minutes; flaky test rate 7%.
- Deploys happen twice per week through a manual approval step; rollback is manual.
- Infrastructure is defined in Terraform, but ledger-db and the Redis cluster were created by hand and are not
  in Terraform state.

## Ownership

- notification-worker, fraud-scorer feature pipeline and the legacy merchant console have no named owning team in
  CODEOWNERS.
- 62% of commits to ledger-service in the last 12 months came from one engineer.
