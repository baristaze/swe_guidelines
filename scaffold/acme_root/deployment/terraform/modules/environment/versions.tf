terraform {
  required_version = ">= 1.11" # ephemeral values and write-only attributes

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 6.0"
      # CloudFront reads certificates from us-east-1 only, whatever the
      # environment's region is; the root passes that provider in.
      configuration_aliases = [aws.us_east_1]
    }
    random = {
      source  = "hashicorp/random"
      version = ">= 3.6"
    }
  }
}
