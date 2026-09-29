# The portal's Content-Security-Policy: the page's own origin, which serves
# the API too, its socket on the same host, the error reporter when one is
# configured, and nothing else. Runs offline: `terraform test` in this folder.

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

run "names_the_page_and_its_socket_and_nothing_else" {
  command = plan

  assert {
    condition     = aws_cloudfront_response_headers_policy.security.security_headers_config[0].content_security_policy[0].content_security_policy == "default-src 'self'; connect-src 'self' wss://app.example.test; img-src 'self' data:; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
    error_message = "the API is the page's own origin: the policy names the page and its socket, and nothing else"
  }

  assert {
    condition     = aws_cloudfront_response_headers_policy.security.security_headers_config[0].content_security_policy[0].override == true
    error_message = "the policy must replace whatever the origin sends"
  }
}

run "names_the_error_reporter_when_one_is_configured" {
  command = plan

  variables {
    sentry_dsn = "https://0123456789abcdef@errors.example.test/7"
  }

  assert {
    condition     = strcontains(aws_cloudfront_response_headers_policy.security.security_headers_config[0].content_security_policy[0].content_security_policy, "connect-src 'self' wss://app.example.test https://errors.example.test;")
    error_message = "the error reporter's origin, and only its origin, joins connect-src"
  }
}

run "refuses_a_dsn_that_is_not_one" {
  command = plan

  variables {
    sentry_dsn = "errors.example.test"
  }

  expect_failures = [var.sentry_dsn]
}

run "keeps_the_portal_config_and_its_client_routes" {
  command = plan

  assert {
    condition     = jsondecode(aws_s3_object.config[0].content).apiUrl == "" && length(aws_cloudfront_function.spa_routes) == 1
    error_message = "the portal reads /config.json and routes client paths to index.html"
  }

  assert {
    condition     = aws_cloudfront_origin_access_control.this.name == "acme-test-portal" && aws_cloudfront_function.spa_routes[0].name == "acme-test-portal-routes"
    error_message = "the portal's resources keep the names they had"
  }
}

run "names_the_object_store_a_signed_url_points_at" {
  command = plan

  variables {
    store_origins = ["https://acme-test-user-file-uploads.s3.us-east-2.amazonaws.com"]
  }

  assert {
    condition     = strcontains(aws_cloudfront_response_headers_policy.security.security_headers_config[0].content_security_policy[0].content_security_policy, "connect-src 'self' wss://app.example.test https://acme-test-user-file-uploads.s3.us-east-2.amazonaws.com;")
    error_message = "the page posts files to the store, so its origin joins connect-src, and nothing broader"
  }

  assert {
    condition     = aws_cloudfront_response_headers_policy.security.security_headers_config[0].content_security_policy[0].content_security_policy == "default-src 'self'; connect-src 'self' wss://app.example.test https://acme-test-user-file-uploads.s3.us-east-2.amazonaws.com; img-src 'self' data: https://acme-test-user-file-uploads.s3.us-east-2.amazonaws.com; media-src 'self' https://acme-test-user-file-uploads.s3.us-east-2.amazonaws.com; frame-src 'self' https://acme-test-user-file-uploads.s3.us-east-2.amazonaws.com; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
    error_message = "a preview loads from the store: its origin joins img-src, media-src, and frame-src, and nothing else changes"
  }
}

run "refuses_a_store_origin_with_a_wildcard" {
  command = plan

  variables {
    store_origins = ["https://*.amazonaws.com"]
  }

  expect_failures = [var.store_origins]
}
