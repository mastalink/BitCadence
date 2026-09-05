# ── Identity ────────────────────────────────────────────────────────────────

variable "name" {
  description = "Short name for this demo city. Prefixes every resource."
  type        = string
  default     = "bitcadence-epcot"
}

variable "region" {
  description = "AWS region. Bedrock model access must be enabled here."
  type        = string
  default     = "us-east-1"
}

variable "console_hostname" {
  description = "DNS hostname pointing at the ALB; must match the ACM certificate."
  type        = string
}

variable "certificate_arn" {
  description = "Issued ACM certificate in this region for console_hostname."
  type        = string
}

variable "image_tag" {
  description = "Immutable release or git commit tag published to all three ECR repositories."
  type        = string
  validation {
    condition     = length(var.image_tag) > 0 && var.image_tag != "latest"
    error_message = "Use an immutable build tag, not latest."
  }
}

# ── The core: which store holds the ledger ──────────────────────────────────

variable "store_backend" {
  description = <<-EOT
    "postgres" runs RDS Postgres behind PostgREST so the gateway's Supabase
    client can reach it (Team/Enterprise posture). "local" runs the embedded
    LocalStore (SQLite) on EFS with a single gateway task (Appliance posture).
    "local" is the guaranteed-first-apply path; "postgres" is the one that
    proves the enterprise story and carries one seam to verify on first run
    (see docs/DEMO-epcot.md, "First-apply risks").
  EOT
  type        = string
  default     = "postgres"
  validation {
    condition     = contains(["postgres", "local"], var.store_backend)
    error_message = "store_backend must be \"postgres\" or \"local\"."
  }
}

variable "db_instance_class" {
  type    = string
  default = "db.t4g.small"
}

# ── Workers (the ring) ──────────────────────────────────────────────────────

variable "bedrock_model_id" {
  description = <<-EOT
    Bedrock model (or inference profile) ID the cloud workers invoke. Set this
    to a model you have enabled in the Bedrock console for var.region. The
    default is a placeholder that will fail loudly at first job, which is
    preferable to a silent wrong model.
  EOT
  type        = string
  default     = "SET-ME-anthropic-model-id"
}

variable "worker_roles" {
  description = "Roles to run as Bedrock-backed cloud workers. One Fargate task each."
  type        = list(string)
  default     = ["claude", "reviewer", "servicenow", "dynatrace"]
}

# ── The industrial park: partner tenants (created by you, wired by this) ────

variable "servicenow_instance_url" {
  description = "e.g. https://dev12345.service-now.com (a Personal Developer Instance is fine). Empty disables the pavilion."
  type        = string
  default     = ""
}
variable "servicenow_username" {
  type    = string
  default = ""
}
variable "servicenow_password" {
  type      = string
  default   = ""
  sensitive = true
}

variable "dynatrace_base_url" {
  description = "e.g. https://abc12345.live.dynatrace.com (a trial tenant is fine). Empty disables the pavilion."
  type        = string
  default     = ""
}
variable "dynatrace_api_token" {
  description = "Scopes: problems.read, problems.write, metrics.ingest (for the OTLP metrics path)."
  type        = string
  default     = ""
  sensitive   = true
}

# ── Evidence and alerting ───────────────────────────────────────────────────

variable "evidence_retention_days" {
  description = "S3 Object Lock COMPLIANCE retention. Nobody - including root - can delete an evidence object before this elapses. Choose deliberately."
  type        = number
  default     = 365
}

variable "alert_email" {
  description = "Dead-man and chaos alerts go here. You must confirm the SNS subscription email once."
  type        = string
}

variable "alert_sms" {
  description = "E.164 phone number for SMS alerts, e.g. +14405551234. Empty skips SMS (SNS SMS may require sandbox exit)."
  type        = string
  default     = ""
}

# ── The clock: how often the city tests itself ──────────────────────────────

variable "chaos_schedule" {
  description = "EventBridge Scheduler expression for the conductor. A city that only tests itself when someone is watching is a museum."
  type        = string
  default     = "rate(6 hours)"
}

variable "grafana_admin_sso_user_ids" {
  description = "IAM Identity Center user IDs granted ADMIN in Managed Grafana. Empty means you assign in the console after apply."
  type        = list(string)
  default     = []
}
