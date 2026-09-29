terraform {
  required_version = ">= 1.11" # the S3 backend's use_lockfile; ephemeral values and write-only attributes

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }

  # Partial configuration: bucket, key, and region arrive as -backend-config
  # arguments (see ../../modules/README.md). CI validates with -backend=false.
  backend "s3" {}
}

# Each environment has an account of its own, and a credential of one cannot
# reach the other. Every resource also carries the environment as a tag, and
# the deploy roles deny anything tagged as the other one, which holds even if
# a root is ever applied in the wrong account.
provider "aws" {
  region = var.region

  # The environment's own account and no other, whatever credential applies.
  allowed_account_ids = [jsondecode(file("${path.module}/../../../cloud/environments.json")).environments.production.account_id]

  default_tags {
    tags = {
      "acme:managed-by"  = "terraform"
      "acme:environment" = var.environment
    }
  }
}

# CloudFront reads certificates from us-east-1 only, whatever var.region is.
provider "aws" {
  alias  = "us_east_1"
  region = "us-east-1"

  allowed_account_ids = [jsondecode(file("${path.module}/../../../cloud/environments.json")).environments.production.account_id]

  default_tags {
    tags = {
      "acme:managed-by"  = "terraform"
      "acme:environment" = var.environment
    }
  }
}
