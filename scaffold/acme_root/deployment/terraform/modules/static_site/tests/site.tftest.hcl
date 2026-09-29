# The company site through the same module: no API, so the page reaches its
# own origin alone; no runtime config; no client routes; and a missing path
# answers with the site's own not-found page. Runs offline: `terraform test`
# in this folder.

mock_provider "aws" {}

variables {
  name            = "site"
  environment     = "test"
  bucket_name     = "acme-test-site"
  domain_name     = "example.test"
  certificate_arn = "arn:aws:acm:us-east-1:123456789012:certificate/test"
  not_found_page  = "/404.html"
}

run "reaches_its_own_origin_and_nothing_else" {
  command = plan

  assert {
    condition     = aws_cloudfront_response_headers_policy.security.security_headers_config[0].content_security_policy[0].content_security_policy == "default-src 'self'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
    error_message = "a page that calls no API reaches its own origin alone"
  }
}

run "writes_no_config_and_routes_nothing" {
  command = plan

  assert {
    condition     = length(aws_s3_object.config) == 0 && length(aws_cloudfront_function.spa_routes) == 0
    error_message = "the site has no runtime config and no client routes"
  }

  assert {
    condition     = length(aws_cloudfront_distribution.this.default_cache_behavior[0].function_association) == 0
    error_message = "no function runs on a viewer request"
  }

  assert {
    condition     = length(aws_cloudfront_distribution.this.ordered_cache_behavior) == 0 && length(aws_cloudfront_distribution.this.origin) == 1
    error_message = "the site serves its bucket alone: no API origin, no API behavior"
  }
}

run "a_missing_path_gets_the_not_found_page" {
  command = plan

  assert {
    condition = alltrue([
      for response in aws_cloudfront_distribution.this.custom_error_response :
      response.response_code == 404 && response.response_page_path == "/404.html"
    ]) && length(aws_cloudfront_distribution.this.custom_error_response) == 2
    error_message = "403 and 404 from the bucket both become /404.html with a 404"
  }
}

run "names_its_resources_by_what_it_is" {
  command = plan

  assert {
    condition     = aws_cloudfront_origin_access_control.this.name == "acme-test-site" && aws_cloudfront_response_headers_policy.security.name == "acme-test-site-security"
    error_message = "the site's resources are named apart from the portal's"
  }
}

run "refuses_a_not_found_page_that_is_not_a_path" {
  command = plan

  variables {
    not_found_page = "404.html"
  }

  expect_failures = [var.not_found_page]
}
