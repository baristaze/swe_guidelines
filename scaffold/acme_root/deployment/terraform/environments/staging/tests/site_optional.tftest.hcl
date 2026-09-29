# The company site is optional: with no site name the whole environment
# plans, the API, the workers, and the portal as always, with no site and
# no certificate lookup; with a name the site joins it. The same plan finds
# the migration inputs in the checkout. Runs offline under
# mock providers, whose values stand in for what AWS would answer:
# `terraform test` in this folder.

mock_provider "aws" {
  mock_data "aws_partition" {
    defaults = { partition = "aws", dns_suffix = "amazonaws.com" }
  }
  mock_data "aws_caller_identity" {
    defaults = { account_id = "123456789012" }
  }
  mock_data "aws_region" {
    defaults = { name = "us-west-2", region = "us-west-2" }
  }
  mock_data "aws_iam_policy_document" {
    defaults = { json = "{\"Version\":\"2012-10-17\",\"Statement\":[]}" }
  }
  mock_resource "aws_acm_certificate_validation" {
    defaults = { certificate_arn = "arn:aws:acm:us-east-1:123456789012:certificate/test" }
  }
  mock_resource "aws_acm_certificate" {
    override_during = plan
    defaults = {
      arn = "arn:aws:acm:us-east-1:123456789012:certificate/test"
      domain_validation_options = [
        { domain_name = "example.test", resource_record_name = "_x.example.test.", resource_record_type = "CNAME", resource_record_value = "_y.acm-validations.aws." },
      ]
    }
  }
  mock_data "aws_availability_zones" {
    defaults = { names = ["us-west-2a", "us-west-2b"] }
  }
}

mock_provider "aws" {
  alias = "us_east_1"

  mock_resource "aws_acm_certificate_validation" {
    defaults = { certificate_arn = "arn:aws:acm:us-east-1:123456789012:certificate/test" }
  }
  mock_resource "aws_acm_certificate" {
    override_during = plan
    defaults = {
      arn = "arn:aws:acm:us-east-1:123456789012:certificate/test"
      domain_validation_options = [
        { domain_name = "example.test", resource_record_name = "_x.example.test.", resource_record_type = "CNAME", resource_record_value = "_y.acm-validations.aws." },
      ]
    }
  }
  mock_data "aws_iam_policy_document" {
    defaults = { json = "{\"Version\":\"2012-10-17\",\"Statement\":[]}" }
  }
  mock_data "aws_acm_certificate" {
    defaults = { arn = "arn:aws:acm:us-east-1:123456789012:certificate/site" }
  }
}

# random is the real provider: it runs offline, and the mocks do not
# support its ephemeral passwords.

variables {
  api_image         = "123456789012.dkr.ecr.us-west-2.amazonaws.com/acme-api@sha256:0000000000000000000000000000000000000000000000000000000000000000"
  maintenance_image = "123456789012.dkr.ecr.us-west-2.amazonaws.com/acme-maintenance@sha256:0000000000000000000000000000000000000000000000000000000000000000"
  api_domain_name   = "api.staging.example.test"
  app_domain_name   = "app.staging.example.test"
  alarm_email       = "alarms@example.test"
}

run "no_site_name_plans_everything_else" {
  command = plan

  assert {
    condition     = output.site_url == "" && output.site_bucket == "" && output.site_distribution_id == ""
    error_message = "with no site name there is no site"
  }

  assert {
    condition     = output.portal_url == "https://app.staging.example.test" && output.api_url == "https://api.staging.example.test"
    error_message = "the portal and the API are there as always"
  }

  assert {
    condition     = length([for f in module.environment.migration_files : f if startswith(f, "om/migrations/sql/")]) > 0
    error_message = "the migration fingerprint covers the migrations' SQL, found from the checkout"
  }
}

run "a_site_name_adds_the_site" {
  command = plan

  variables {
    site_domain_name = "staging.example.test"
  }

  assert {
    condition     = output.site_url == "https://staging.example.test"
    error_message = "a site name makes the site"
  }
}
