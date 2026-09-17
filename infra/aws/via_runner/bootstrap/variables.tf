variable "aws_profile" {
  type        = string
  default     = "batoncadence"
  description = "Provisioning profile only; it is not stored in cloud resources."
}

variable "region" {
  type    = string
  default = "us-east-1"
}
