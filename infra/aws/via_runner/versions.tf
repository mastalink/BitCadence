terraform {
  required_version = ">= 1.6"

  # Backend configuration is supplied by the bootstrap output. It must be
  # initialized before any runner apply; local state is not an accepted runner
  # control plane.
  backend "s3" {}

  required_providers {
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.5"
    }
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.70"
    }
  }
}

provider "aws" {
  # Empty uses one-process environment credentials during provisioning. The
  # runner itself always uses its Lambda role and never this profile.
  profile = var.aws_profile == "" ? null : var.aws_profile
  region  = var.region

  default_tags {
    tags = {
      Project   = "via"
      Component = "bitcadence-score-runner"
      ManagedBy = "terraform"
    }
  }
}
