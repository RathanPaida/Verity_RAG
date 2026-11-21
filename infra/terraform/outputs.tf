output "service_url" {
  value = google_cloud_run_v2_service.api.uri
}

output "artifact_registry" {
  value = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.verity.repository_id}"
}

output "raw_bucket" {
  value = google_storage_bucket.raw.name
}

output "github_actions_variables" {
  description = "Set these as repository variables for .github/workflows/deploy.yml"
  value = {
    GCP_PROJECT      = var.project_id
    GCP_REGION       = var.region
    GCP_WIF_PROVIDER = google_iam_workload_identity_pool_provider.github.name
    GCP_DEPLOY_SA    = google_service_account.deployer.email
  }
}

output "secrets_to_populate" {
  description = "printf '%s' VALUE | gcloud secrets versions add NAME --data-file=-"
  value       = [for s in google_secret_manager_secret.s : s.secret_id]
}
