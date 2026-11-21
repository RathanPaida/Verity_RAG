variable "project_id" {
  description = "GCP project that hosts Verity"
  type        = string
}

variable "region" {
  description = "Region for Cloud Run, Artifact Registry and the raw-source bucket (co-locate with Atlas)"
  type        = string
  default     = "us-central1"
}

variable "image" {
  description = "Initial container image; CI/CD rolls out new revisions after that"
  type        = string
  default     = "us-docker.pkg.dev/cloudrun/container/hello"
}

variable "github_repository" {
  description = "owner/repo allowed to deploy through Workload Identity Federation"
  type        = string
  default     = "RathanPaida/Verity_RAG"
}

variable "gemini_backend" {
  description = "vertex (service-account auth) or api (Gemini API key from Secret Manager)"
  type        = string
  default     = "vertex"
  validation {
    condition     = contains(["vertex", "api"], var.gemini_backend)
    error_message = "gemini_backend must be vertex or api."
  }
}

variable "min_instances" {
  description = "Warm instances (0 = scale to zero; 1 removes cold starts)"
  type        = number
  default     = 0
}

variable "max_instances" {
  description = "Instance ceiling; with the per-instance rate limit this bounds model spend"
  type        = number
  default     = 5
}

variable "alert_email" {
  description = "Cloud Monitoring alert recipient; empty disables alerting"
  type        = string
  default     = ""
}

variable "billing_account" {
  description = "Billing account ID for the budget alert; empty disables the budget"
  type        = string
  default     = ""
}

variable "monthly_budget_usd" {
  description = "Monthly budget that triggers alerts at 50/90/100 percent"
  type        = number
  default     = 20
}
