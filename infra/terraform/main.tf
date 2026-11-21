locals {
  services = [
    "run.googleapis.com", "aiplatform.googleapis.com", "secretmanager.googleapis.com",
    "artifactregistry.googleapis.com", "eventarc.googleapis.com", "storage.googleapis.com",
    "cloudtrace.googleapis.com", "monitoring.googleapis.com", "logging.googleapis.com",
    "iamcredentials.googleapis.com", "sts.googleapis.com", "pubsub.googleapis.com",
    "billingbudgets.googleapis.com",
  ]

  secret_env = merge(
    { MONGODB_URI = "verity-mongodb-uri", VERITY_API_KEY = "verity-api-key" },
    var.gemini_backend == "api" ? { GEMINI_API_KEY = "verity-gemini-api-key" } : {},
  )

  env = {
    ENV                   = "production"
    GOOGLE_CLOUD_PROJECT  = var.project_id
    GOOGLE_CLOUD_LOCATION = var.region
    GEMINI_BACKEND        = var.gemini_backend
    LLM_PROVIDER          = "gemini"
    EMBEDDING_PROVIDER    = "gemini"
    CHECKPOINTER          = "mongodb"
    OTEL_EXPORTER         = "gcp"
    LOG_FORMAT            = "json"
    EMBEDDING_CACHE_PATH  = "/tmp/verity/embeddings.sqlite"
  }
}

resource "google_project_service" "apis" {
  for_each           = toset(local.services)
  service            = each.value
  disable_on_destroy = false
}

# ------------------------------------------------------------------ images
resource "google_artifact_registry_repository" "verity" {
  repository_id = "verity"
  location      = var.region
  format        = "DOCKER"
  cleanup_policies {
    id     = "keep-recent"
    action = "KEEP"
    most_recent_versions {
      keep_count = 20
    }
  }
  depends_on = [google_project_service.apis]
}

# ------------------------------------------------------------------ identities (least privilege)
resource "google_service_account" "runtime" {
  account_id   = "verity-run"
  display_name = "Verity Cloud Run runtime"
}

resource "google_project_iam_member" "runtime" {
  for_each = toset([
    "roles/aiplatform.user", "roles/cloudtrace.agent", "roles/logging.logWriter",
    "roles/monitoring.metricWriter", "roles/eventarc.eventReceiver",
  ])
  project = var.project_id
  role    = each.value
  member  = "serviceAccount:${google_service_account.runtime.email}"
}

resource "google_service_account" "deployer" {
  account_id   = "verity-deploy"
  display_name = "Verity CI/CD deployer (GitHub Actions via WIF)"
}

resource "google_project_iam_member" "deployer" {
  for_each = toset(["roles/run.developer", "roles/artifactregistry.writer"])
  project  = var.project_id
  role     = each.value
  member   = "serviceAccount:${google_service_account.deployer.email}"
}

# the deployer may create revisions that run as the runtime SA, and mint ID tokens for smoke tests
resource "google_service_account_iam_member" "deployer_actas_runtime" {
  service_account_id = google_service_account.runtime.name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${google_service_account.deployer.email}"
}

resource "google_service_account_iam_member" "deployer_token_self" {
  service_account_id = google_service_account.deployer.name
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = "serviceAccount:${google_service_account.deployer.email}"
}

# ------------------------------------------------------------------ secrets (values are added out of band)
resource "google_secret_manager_secret" "s" {
  for_each  = toset(values(local.secret_env))
  secret_id = each.value
  replication {
    auto {}
  }
  depends_on = [google_project_service.apis]
}

resource "google_secret_manager_secret_iam_member" "runtime" {
  for_each  = google_secret_manager_secret.s
  secret_id = each.value.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.runtime.email}"
}

# ------------------------------------------------------------------ raw sources bucket
resource "google_storage_bucket" "raw" {
  name                        = "${var.project_id}-verity-raw"
  location                    = var.region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  versioning {
    enabled = true
  }
  lifecycle_rule {
    condition {
      num_newer_versions = 5
    }
    action {
      type = "Delete"
    }
  }
}

resource "google_storage_bucket_iam_member" "runtime_read" {
  bucket = google_storage_bucket.raw.name
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${google_service_account.runtime.email}"
}

# ------------------------------------------------------------------ API service
resource "google_cloud_run_v2_service" "api" {
  name                = "verity-api"
  location            = var.region
  ingress             = "INGRESS_TRAFFIC_ALL"
  deletion_protection = false

  template {
    service_account                  = google_service_account.runtime.email
    timeout                          = "300s"
    max_instance_request_concurrency = 20
    scaling {
      min_instance_count = var.min_instances
      max_instance_count = var.max_instances
    }
    containers {
      image = var.image
      resources {
        limits            = { cpu = "2", memory = "2Gi" }
        startup_cpu_boost = true
      }
      dynamic "env" {
        for_each = local.env
        content {
          name  = env.key
          value = env.value
        }
      }
      dynamic "env" {
        for_each = local.secret_env
        content {
          name = env.key
          value_source {
            secret_key_ref {
              secret  = google_secret_manager_secret.s[env.value].secret_id
              version = "latest"
            }
          }
        }
      }
      startup_probe {
        http_get {
          path = "/readyz"
        }
        period_seconds    = 5
        failure_threshold = 12
      }
      liveness_probe {
        http_get {
          path = "/healthz"
        }
        period_seconds = 30
      }
    }
  }

  lifecycle {
    # CI/CD owns image rollouts and traffic splits; Terraform owns everything else
    ignore_changes = [template[0].containers[0].image, traffic, client, client_version]
  }
  depends_on = [google_secret_manager_secret_iam_member.runtime, google_project_iam_member.runtime]
}

# private service: only Eventarc (as the runtime SA) and the deployer (smoke tests) may invoke it
resource "google_cloud_run_v2_service_iam_member" "invokers" {
  for_each = {
    eventarc = google_service_account.runtime.email
    deployer = google_service_account.deployer.email
  }
  name     = google_cloud_run_v2_service.api.name
  location = var.region
  role     = "roles/run.invoker"
  member   = "serviceAccount:${each.value}"
}

# ------------------------------------------------------------------ event-driven ingestion
data "google_storage_project_service_account" "gcs" {}

resource "google_project_iam_member" "gcs_pubsub" {
  project = var.project_id
  role    = "roles/pubsub.publisher"
  member  = "serviceAccount:${data.google_storage_project_service_account.gcs.email_address}"
}

resource "google_eventarc_trigger" "gcs_ingest" {
  name            = "verity-gcs-ingest"
  location        = var.region
  service_account = google_service_account.runtime.email
  matching_criteria {
    attribute = "type"
    value     = "google.cloud.storage.object.v1.finalized"
  }
  matching_criteria {
    attribute = "bucket"
    value     = google_storage_bucket.raw.name
  }
  destination {
    cloud_run_service {
      service = google_cloud_run_v2_service.api.name
      region  = var.region
      path    = "/events/gcs"
    }
  }
  depends_on = [google_project_iam_member.gcs_pubsub, google_cloud_run_v2_service_iam_member.invokers]
}

# Bulk backfill: gcloud run jobs execute verity-ingest --args=-m,scripts.ingest,gs://BUCKET/ENG,--engagement,ENG
resource "google_cloud_run_v2_job" "ingest" {
  name                = "verity-ingest"
  location            = var.region
  deletion_protection = false
  template {
    template {
      service_account = google_service_account.runtime.email
      timeout         = "1800s"
      max_retries     = 1
      containers {
        image   = var.image
        command = ["python"]
        args    = ["-m", "scripts.ingest", "gs://${google_storage_bucket.raw.name}/acme-payments", "--engagement", "acme-payments"]
        dynamic "env" {
          for_each = local.env
          content {
            name  = env.key
            value = env.value
          }
        }
        dynamic "env" {
          for_each = local.secret_env
          content {
            name = env.key
            value_source {
              secret_key_ref {
                secret  = google_secret_manager_secret.s[env.value].secret_id
                version = "latest"
              }
            }
          }
        }
      }
    }
  }
  lifecycle {
    ignore_changes = [template[0].template[0].containers[0].image, client, client_version]
  }
  depends_on = [google_secret_manager_secret_iam_member.runtime]
}
