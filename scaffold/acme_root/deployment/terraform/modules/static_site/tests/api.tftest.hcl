# The portal serves the API from its own distribution: the API's paths go to
# its load balancer, uncached, with every method and every viewer header but
# Host, the socket through the same behavior, and the edge secret on every
# request. The static files keep the default behavior. Runs offline:
# `terraform test` in this folder.

mock_provider "aws" {}

variables {
  name            = "portal"
  environment     = "test"
  bucket_name     = "acme-test-portal"
  domain_name     = "app.example.test"
  certificate_arn = "arn:aws:acm:us-east-1:123456789012:certificate/test"
  api_domain_name = "api.example.test"
  api_edge_secret = "0123456789abcdef0123456789abcdef"
  client_routes   = true
  runtime_config  = { apiUrl = "", sentryDsn = "", environment = "test" }
}

run "routes_the_api_paths_to_the_load_balancer_uncached" {
  command = plan

  assert {
    condition     = length(aws_cloudfront_distribution.this.ordered_cache_behavior) == 1
    error_message = "one behavior carries the API: /v1/*, the socket included"
  }

  assert {
    condition = alltrue([
      for behavior in aws_cloudfront_distribution.this.ordered_cache_behavior :
      behavior.path_pattern == "/v1/*" &&
      behavior.target_origin_id == "api" &&
      behavior.cache_policy_id == "4135ea2d-6df8-44a3-9df3-4b5a84be39ad" &&
      behavior.origin_request_policy_id == "b689b0a8-53d0-40ab-baf2-68738e2966ac" &&
      toset(behavior.allowed_methods) == toset(["DELETE", "GET", "HEAD", "OPTIONS", "PATCH", "POST", "PUT"]) &&
      behavior.viewer_protocol_policy == "https-only" &&
      behavior.compress == false &&
      length(behavior.function_association) == 0
    ])
    error_message = "the API behavior caches nothing, forwards every method and every viewer header but Host, never redirects, never compresses, and runs no function"
  }

  assert {
    condition     = aws_cloudfront_distribution.this.default_cache_behavior[0].target_origin_id == "portal" && length(aws_cloudfront_distribution.this.default_cache_behavior[0].function_association) == 1
    error_message = "everything else is the static files, with the client routes"
  }
}

run "reaches_the_api_by_its_own_name_over_https_with_the_edge_secret" {
  command = plan

  assert {
    condition = anytrue([
      for origin in aws_cloudfront_distribution.this.origin :
      origin.origin_id == "api" &&
      origin.domain_name == "api.example.test" &&
      origin.custom_origin_config[0].origin_protocol_policy == "https-only" &&
      anytrue([
        for header in origin.custom_header :
        header.name == "X-Acme-Edge" && header.value == "0123456789abcdef0123456789abcdef"
      ])
    ])
    error_message = "the API origin is its domain name over HTTPS, and every request carries the edge secret"
  }
}

run "waits_as_long_as_the_load_balancer_and_lets_go_of_a_connection_first" {
  command = plan

  assert {
    condition = anytrue([
      for origin in aws_cloudfront_distribution.this.origin :
      origin.origin_id == "api" &&
      origin.custom_origin_config[0].origin_read_timeout == jsondecode(file("../../../realtime-timeouts.json")).load_balancer_idle_timeout_seconds &&
      origin.custom_origin_config[0].origin_keepalive_timeout < jsondecode(file("../../../realtime-timeouts.json")).load_balancer_idle_timeout_seconds
    ])
    error_message = "the read timeout is the load balancer's idle timeout, and a spare connection closes at the edge before the load balancer closes it"
  }
}

run "refuses_a_not_found_page_beside_the_api" {
  command = plan

  variables {
    not_found_page = "/404.html"
  }

  expect_failures = [var.api_domain_name]
}

run "refuses_the_api_without_an_edge_secret" {
  command = plan

  variables {
    api_edge_secret = ""
  }

  expect_failures = [var.api_domain_name]
}

run "refuses_an_api_path_that_is_a_file_of_the_site" {
  command = plan

  variables {
    api_path_patterns = ["/assets/*"]
  }

  expect_failures = [var.api_path_patterns]
}

run "refuses_an_api_name_with_a_scheme" {
  command = plan

  variables {
    api_domain_name = "https://api.example.test"
  }

  expect_failures = [var.api_domain_name]
}
