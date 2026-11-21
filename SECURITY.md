# Security

## Reporting a vulnerability

Please report vulnerabilities privately through GitHub's **Report a vulnerability** (Security Advisories) on
this repository instead of opening a public issue. Include the request ID or a minimal reproduction. You will
get an acknowledgement within 3 business days.

## Threat model and controls

| Threat | Control | Where |
|---|---|---|
| Prompt injection in ingested documents ("ignore previous instructions…") | Retrieved text is delimited and declared as data. Tools are read-only. The record tool accepts a whitelisted JSON spec, never raw MQL. Citations are validated against the evidence set. Injection cases are in the golden set | `agents/prompts.py`, `retrieval/structured.py`, `eval/golden.jsonl` (`inj*`) |
| Cross-client data leakage | Engagement-scoped source and chunk IDs. `engagement_id` pre-filter on every vector, text, aggregation, memory and cache query. Leakage is measured by the eval | ADR 0006, `eval/run_eval.py` |
| Secrets or PII in client documents reaching embeddings or logs | Redaction (keys, tokens, connection strings, emails, card numbers, phone numbers) before chunking and embedding. Off-the-record statements are never stored as memory | `security.py` |
| Unauthenticated access | `X-API-Key` compared in constant time. The Cloud Run service is private (IAM invoker). Eventarc calls with OIDC | `api/main.py`, `infra/terraform` |
| Abuse and cost exhaustion | Per-client token-bucket rate limits on expensive routes, Cloud Run max instances, agent step and retry budgets, billing budget alerts | `api/middleware.py`, `config.py` |
| Error-message information leaks | Catch-all handler returns `internal error` and a request ID. Tracebacks only go to logs | `api/middleware.py` |
| Browser-side attacks on the UI | CSP (`script-src 'self'`, `frame-ancestors 'none'`), nosniff, frame-deny, HSTS over HTTPS. All rendered model text is HTML-escaped | `api/middleware.py`, `api/static/app.js` |
| Upload abuse | Extension allow-list, size limit (`MAX_UPLOAD_MB`), files written to a temp dir that is removed after ingestion | `api/main.py` |
| Supply chain | Dependabot, `pip-audit`, Trivy image scan, gitleaks secret scan, pinned major versions, non-root container | `.github/` |
| Credential sprawl in CI/CD | Workload Identity Federation restricted to this repository. No service-account keys | `infra/terraform/cicd.tf` |

## Secrets handling

- Secrets live only in Secret Manager and are injected as environment variables at revision start.
- They are never put in Terraform variables or state, never in images, and never in the repository
  (`.env` is git-ignored and checked by gitleaks).
- `ENV=production` refuses to start without an API key, with an in-memory database, or with offline model
  providers.
