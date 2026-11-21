# SLO-driven alerting (targets in docs/OPERATIONS.md). Created only when alert_email is set.
locals {
  alerting = var.alert_email != "" ? 1 : 0
  run_filter = join(" AND ", [
    "resource.type = \"cloud_run_revision\"",
    "resource.labels.service_name = \"${google_cloud_run_v2_service.api.name}\"",
  ])
}

resource "google_monitoring_notification_channel" "email" {
  count        = local.alerting
  display_name = "Verity on-call"
  type         = "email"
  labels = {
    email_address = var.alert_email
  }
}

resource "google_monitoring_alert_policy" "error_rate" {
  count        = local.alerting
  display_name = "Verity API 5xx rate > 5% (10 min)"
  combiner     = "OR"
  conditions {
    display_name = "5xx ratio"
    condition_threshold {
      filter             = "${local.run_filter} AND metric.type = \"run.googleapis.com/request_count\" AND metric.labels.response_code_class = \"5xx\""
      comparison         = "COMPARISON_GT"
      threshold_value    = 0.05
      duration           = "600s"
      denominator_filter = join(" AND ", [local.run_filter, "metric.type = \"run.googleapis.com/request_count\""])
      aggregations {
        alignment_period   = "300s"
        per_series_aligner = "ALIGN_RATE"
      }
      denominator_aggregations {
        alignment_period   = "300s"
        per_series_aligner = "ALIGN_RATE"
      }
    }
  }
  notification_channels = [google_monitoring_notification_channel.email[0].id]
  documentation {
    content   = "Runbook: docs/OPERATIONS.md#5xx-spike. Check Logs Explorer for severity>=ERROR grouped by request_id; 502s mean every Gemini model in the fallback chain failed."
    mime_type = "text/markdown"
  }
}

resource "google_monitoring_alert_policy" "latency" {
  count        = local.alerting
  display_name = "Verity /ask p95 latency > 60 s (15 min)"
  combiner     = "OR"
  conditions {
    display_name = "p95 request latency"
    condition_threshold {
      filter          = "${local.run_filter} AND metric.type = \"run.googleapis.com/request_latencies\""
      comparison      = "COMPARISON_GT"
      threshold_value = 60000
      duration        = "900s"
      aggregations {
        alignment_period     = "300s"
        per_series_aligner   = "ALIGN_PERCENTILE_95"
        cross_series_reducer = "REDUCE_MAX"
      }
    }
  }
  notification_channels = [google_monitoring_notification_channel.email[0].id]
  documentation {
    content   = "Runbook: docs/OPERATIONS.md#latency. Usually model rate limiting (429 cooldowns in the logs) or Atlas search latency."
    mime_type = "text/markdown"
  }
}

# Log-based metric: answers served by a fallback model (primary quota/overload); spikes precede latency alerts
resource "google_logging_metric" "fallback_served" {
  name   = "verity/llm_fallback_served"
  filter = "${local.run_filter} AND jsonPayload.logger = \"verity.llm\" AND jsonPayload.message =~ \"served by fallback model\""
  metric_descriptor {
    metric_kind = "DELTA"
    value_type  = "INT64"
  }
}

resource "google_billing_budget" "monthly" {
  count           = var.billing_account != "" ? 1 : 0
  billing_account = var.billing_account
  display_name    = "Verity monthly budget"
  budget_filter {
    projects = ["projects/${data.google_project.this.number}"]
  }
  amount {
    specified_amount {
      currency_code = "USD"
      units         = tostring(var.monthly_budget_usd)
    }
  }
  dynamic "threshold_rules" {
    for_each = [0.5, 0.9, 1.0]
    content {
      threshold_percent = threshold_rules.value
    }
  }
}

data "google_project" "this" {}
