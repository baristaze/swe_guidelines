terraform {
  required_version = ">= 1.10" # the S3 backend's use_lockfile

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }

  # Partial configuration: bucket, key, and region arrive as -backend-config
  # arguments from scripts/cloud_create.sh. CI validates with -backend=false.
  backend "s3" {}
}

provider "aws" {
  region = local.config.region

  # Every call is refused outside production's account, whatever profile the
  # person holds: the script checks the identity first, and this checks it
  # again at the provider.
  allowed_account_ids = [local.production.account_id]

  default_tags {
    tags = {
      "acme:managed-by"  = "terraform"
      "acme:environment" = "production"
    }
  }
}

# CloudFront reads certificates from us-east-1 only: the company site's is
# made there.
provider "aws" {
  alias  = "us_east_1"
  region = "us-east-1"

  allowed_account_ids = [local.production.account_id]

  default_tags {
    tags = {
      "acme:managed-by"  = "terraform"
      "acme:environment" = "production"
    }
  }
}
