terraform {
  required_version = ">= 1.11" # write-only attributes

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 6.0"
    }
  }
}
