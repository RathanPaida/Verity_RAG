---
title: Internal Security Review 2024
kind: security
owner: acme-security
doc_date: 2024-09-10
---
# Internal Security Review 2024: Payments Platform

## Scope

Payments services, CI/CD pipeline, cloud IAM and secrets handling. Conducted by the internal security team
over two weeks in August 2024.

## Findings

### SEC-01 Long-lived service account keys (High)

Twelve service account JSON keys are stored as Kubernetes secrets. The oldest key was created 431 days ago and
has never been rotated. Keys grant roles/editor on the payments project.

### SEC-02 Admin console without MFA (High)

The legacy merchant admin console uses username and password only. It is reachable from the public internet.

### SEC-03 Outdated dependency (Medium)

PAY-API depends on an HTTP library version with a published request-smuggling vulnerability (CVE-2023-44487
class issue). Upgrade is planned for Q4.

### SEC-04 Logging of card data (Low)

Debug logging in fraud-scorer wrote truncated card numbers (first 6, last 4) to Cloud Logging. Fixed in August.

## Positive Observations

- All service-to-service traffic uses mTLS.
- Production access requires just-in-time elevation with approval.
