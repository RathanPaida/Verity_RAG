# Infrastructure (Terraform, Google Cloud)

`infra/terraform` provisions everything Verity needs on GCP. It is validated in CI (`terraform fmt -check`, `terraform validate`).

| File | Resources |
|---|---|
| `main.tf` | APIs, Artifact Registry (cleanup policy), runtime + deployer service accounts (least privilege), Secret Manager secrets, raw-source GCS bucket (versioned, public access blocked), Cloud Run service (private, startup/liveness probes, secrets as env), Eventarc GCS→`/events/gcs` trigger, Cloud Run backfill Job |
| `cicd.tf` | Workload Identity Federation pool/provider restricted to this GitHub repository (no service-account keys) |
| `monitoring.tf` | Email channel, 5xx-ratio and p95-latency alert policies with runbook links, log-based metric for fallback-model answers, billing budget at 50/90/100% |
| `outputs.tf` | Service URL, registry path, bucket, the GitHub Actions variables to set, secrets to populate |

## Bring-up

```bash
cd infra/terraform
cp terraform.tfvars.example terraform.tfvars        # project_id, region, alert_email, ...
gsutil mb -l us-central1 gs://<project>-tfstate      # once, for remote state
terraform init -backend-config="bucket=<project>-tfstate"
terraform apply

# secret values never go through Terraform state
printf '%s' "$MONGODB_URI" | gcloud secrets versions add verity-mongodb-uri --data-file=-
printf '%s' "$(openssl rand -hex 24)" | gcloud secrets versions add verity-api-key --data-file=-

# set the `github_actions_variables` output as repository variables, then tag a release:
git tag v1.1.0 && git push origin v1.1.0          # .github/workflows/deploy.yml rolls it out
```

Rollouts and traffic splits belong to CI/CD (`deploy.yml`: tagged no-traffic revision → smoke test → traffic shift).
Terraform ignores image and traffic changes so the two never fight. `deploy/deploy.sh` is the imperative
equivalent for a one-off project without Terraform.
