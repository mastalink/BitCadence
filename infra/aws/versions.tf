terraform {
  required_version = ">= 1.6"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.70"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }

  # Remote state is deliberately not configured. First run uses local state so
  # nothing has to exist before `terraform init`. Move it to S3 + DynamoDB once
  # the stack is proven; the evidence bucket is NOT the place for state.
}

provider "aws" {
  region = var.region

  default_tags {
    tags = {
      Project   = "bitcadence-epcot"
      ManagedBy = "terraform"
      # The whole demo is one tag away from teardown. Keep it that way.
      Demo = var.name
    }
  }
}
