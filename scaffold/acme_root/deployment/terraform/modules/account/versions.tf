terraform {
  required_version = ">= 1.9"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 6.0"
      # CloudFront reads certificates from us-east-1 only: the company
      # site's is requested there.
      configuration_aliases = [aws.us_east_1]
    }
  }
}
