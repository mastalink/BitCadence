variable "aws_profile" {
  description = "Named operator profile used only by Terraform during provisioning; it is never deployed into AWS."
  type        = string
  default     = "batoncadence"
}

variable "region" {
  description = "VIA's existing AWS region."
  type        = string
  default     = "us-east-1"
}

variable "name" {
  description = "Stable prefix for the isolated runner resources."
  type        = string
  default     = "via-score"
}

variable "via_instance_id" {
  description = "Existing VIA host inspected by the audit. No command execution permission is granted."
  type        = string
}

variable "via_stack_name" {
  description = "Existing VIA CloudFormation stack inspected by the audit."
  type        = string
  default     = "via-pilot"
}

variable "evidence_bucket" {
  description = "Existing VIA evidence bucket. The runner may list backups and append sanitized audit reports only."
  type        = string
}

variable "public_api_base" {
  description = "HTTPS VIA API base. The handler has no event-controlled URL input."
  type        = string
  default     = "https://d3ao3uhnewexzf.cloudfront.net/api"
}

variable "schedule_expression" {
  description = "EventBridge cadence for the read-only evidence refresh."
  type        = string
  default     = "rate(6 hours)"
}

variable "log_retention_days" {
  description = "Runner log retention; reports live in evidence storage, not logs."
  type        = number
  default     = 30
}
