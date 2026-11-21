terraform {
  required_version = ">= 1.6"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.0"
    }
  }
  # Remote state: terraform init -backend-config="bucket=<state-bucket>"
  backend "gcs" {
    prefix = "verity"
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}
