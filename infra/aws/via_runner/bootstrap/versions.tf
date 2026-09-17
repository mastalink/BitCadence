terraform {
  required_version = ">= 1.6"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.70"
    }
  }
}

provider "aws" {
  profile = var.aws_profile == "" ? null : var.aws_profile
  region  = var.region

  default_tags {
    tags = {
      Project   = "via"
      Component = "bitcadence-score-state"
      ManagedBy = "terraform"
    }
  }
}
