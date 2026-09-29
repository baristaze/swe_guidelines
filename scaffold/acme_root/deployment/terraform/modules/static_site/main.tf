# A static site in the cloud: built files in a private bucket, served by a
# CloudFront distribution at one domain name, under the security headers.
# Two sites use it. The portal (name "portal") routes client paths to
# index.html, reads /config.json, written here per environment, so production
# serves the exact files staging already served, and reaches the API through
# this same distribution: the API's paths go to its load balancer, so every
# call the page makes is same-origin and no browser sends a preflight. The
# company site (name "site") calls nothing, has one page and a 404 page, and
# carries its environment's links in its build.

locals {
  tags         = { "acme:environment" = var.environment }
  s3_origin_id = var.name

  # Managed policies; the IDs are fixed across accounts.
  caching_optimized = "658327ea-f89d-4fab-a63d-7e88639e58f6"
  caching_disabled  = "4135ea2d-6df8-44a3-9df3-4b5a84be39ad"
  # Every viewer header, cookie, and query string but Host, so the Bearer
  # header, the socket's upgrade headers, and its ticket reach the API, and
  # CloudFront names the API's own domain in Host and in the TLS handshake,
  # which the load balancer's certificate matches.
  all_viewer_except_host = "b689b0a8-53d0-40ab-baf2-68738e2966ac"

  # The API behind this distribution, when the site has one.
  api_enabled   = var.api_domain_name != ""
  api_origin_id = "api"
  # The load balancer's idle timeout is pinned beside the realtime pings in
  # one shared file. CloudFront waits for the API as long as the load
  # balancer does, so the two agree on when a request has stalled; and it
  # drops a spare connection a few seconds before the load balancer would,
  # so it never sends a request down a connection that is being closed.
  realtime_timeouts     = jsondecode(file("${path.module}/../../../realtime-timeouts.json"))
  api_read_timeout      = local.realtime_timeouts.load_balancer_idle_timeout_seconds
  api_keepalive_timeout = local.realtime_timeouts.load_balancer_idle_timeout_seconds - 5

  # What the page may reach: its own origin (the API included, when it is
  # behind this distribution; its socket is named as wss:// on the same host
  # as well, for a browser that does not read 'self' as the websocket's
  # scheme too), the object store a signed form or link names, and the error
  # reporter (the DSN is scheme://key@host/project; only its origin is
  # named). Nothing else, so a script the site did not ship neither runs nor
  # phones home. Neither build has an inline script or style, so no unsafe
  # directive is needed.
  socket_origin = local.api_enabled ? ["wss://${var.domain_name}"] : []
  sentry_origin = var.sentry_dsn == "" ? [] : [join("", regex("^(https?://)[^@/]+@([^/]+)", var.sentry_dsn))]
  connect_src   = concat(["'self'"], local.socket_origin, var.store_origins, local.sentry_origin)
  # A file's preview loads from the store by a signed inline link: an image,
  # a video or a sound in the page's player, a PDF in a frame. So the store's
  # origin joins img-src, and media-src and frame-src name it, when there is
  # one; with none they are left to default-src.
  store_src = join("", [for origin in var.store_origins : " ${origin}"])
  content_security_policy = join("; ", concat(
    [
      "default-src 'self'",
      "connect-src ${join(" ", local.connect_src)}",
      "img-src 'self' data:${local.store_src}",
    ],
    length(var.store_origins) == 0 ? [] : [
      "media-src 'self'${local.store_src}",
      "frame-src 'self'${local.store_src}",
    ],
    [
      "frame-ancestors 'none'",
      "base-uri 'self'",
      "form-action 'self'",
    ],
  ))
}

resource "aws_s3_bucket" "this" {
  bucket = var.bucket_name
  # The nuke's apply turns this on first, so its destroy empties the build.
  force_destroy = var.destroyable
  tags          = local.tags
}

resource "aws_s3_bucket_public_access_block" "this" {
  bucket                  = aws_s3_bucket.this.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "this" {
  bucket = aws_s3_bucket.this.id

  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "this" {
  bucket = aws_s3_bucket.this.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

# Only this distribution reads the bucket, signing each request (OAC).
resource "aws_cloudfront_origin_access_control" "this" {
  name                              = "acme-${var.environment}-${var.name}"
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

data "aws_iam_policy_document" "read_from_cloudfront" {
  statement {
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.this.arn}/*"]

    principals {
      type        = "Service"
      identifiers = ["cloudfront.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "AWS:SourceArn"
      values   = [aws_cloudfront_distribution.this.arn]
    }
  }
}

resource "aws_s3_bucket_policy" "this" {
  bucket = aws_s3_bucket.this.id
  policy = data.aws_iam_policy_document.read_from_cloudfront.json
}

# The runtime config a page fetches before it renders, when it has one (the
# portal). no-cache: a change reaches browsers on their next load without an
# invalidation.
resource "aws_s3_object" "config" {
  count = length(var.runtime_config) > 0 ? 1 : 0

  bucket        = aws_s3_bucket.this.id
  key           = "config.json"
  content_type  = "application/json"
  cache_control = "no-cache"
  content       = jsonencode(var.runtime_config)
}

moved {
  from = aws_s3_object.config
  to   = aws_s3_object.config[0]
}

# Client routes (/settings, ...) have no file; they get index.html. A path with
# a file extension is a real object and passes through. A site without client
# routes answers a missing path with its not-found page instead (below).
resource "aws_cloudfront_function" "spa_routes" {
  count = var.client_routes ? 1 : 0

  name    = "acme-${var.environment}-${var.name}-routes"
  runtime = "cloudfront-js-2.0"
  comment = "Serves index.html for client-side routes"
  publish = true
  code    = <<-JS
    function handler(event) {
      var request = event.request;
      var last = request.uri.split("/").pop();
      if (last.indexOf(".") === -1) {
        request.uri = "/index.html";
      }
      return request;
    }
  JS
}

moved {
  from = aws_cloudfront_function.spa_routes
  to   = aws_cloudfront_function.spa_routes[0]
}

resource "aws_cloudfront_response_headers_policy" "security" {
  name    = "acme-${var.environment}-${var.name}-security"
  comment = "Security headers for the ${var.name}"

  security_headers_config {
    content_security_policy {
      content_security_policy = local.content_security_policy
      override                = true
    }

    content_type_options {
      override = true
    }

    frame_options {
      frame_option = "DENY"
      override     = true
    }

    referrer_policy {
      referrer_policy = "strict-origin-when-cross-origin"
      override        = true
    }

    strict_transport_security {
      access_control_max_age_sec = 31536000
      include_subdomains         = true
      override                   = true
    }
  }
}

resource "aws_cloudfront_distribution" "this" {
  enabled             = true
  comment             = "Acme ${var.environment}: the ${var.name}"
  default_root_object = "index.html"
  http_version        = "http2and3"
  is_ipv6_enabled     = true
  price_class         = var.price_class
  aliases             = [var.domain_name]
  tags                = local.tags

  origin {
    origin_id                = local.s3_origin_id
    domain_name              = aws_s3_bucket.this.bucket_regional_domain_name
    origin_access_control_id = aws_cloudfront_origin_access_control.this.id
  }

  # The API, at its own domain name, which its load balancer's certificate
  # names. HTTPS only. Every request carries the edge secret in X-Acme-Edge,
  # which CloudFront sets whatever the viewer sent under that name; the API
  # trusts the address CloudFront appended to X-Forwarded-For only beside it.
  dynamic "origin" {
    for_each = local.api_enabled ? [var.api_domain_name] : []
    content {
      origin_id   = local.api_origin_id
      domain_name = origin.value

      custom_origin_config {
        http_port                = 80
        https_port               = 443
        origin_protocol_policy   = "https-only"
        origin_ssl_protocols     = ["TLSv1.2"]
        origin_read_timeout      = local.api_read_timeout
        origin_keepalive_timeout = local.api_keepalive_timeout
      }

      custom_header {
        name  = "X-Acme-Edge"
        value = var.api_edge_secret
      }
    }
  }

  # Static files: cached at the edge; hashed assets never change, the entry
  # points and config.json revalidate (the deploy sets Cache-Control per file).
  default_cache_behavior {
    target_origin_id           = local.s3_origin_id
    allowed_methods            = ["GET", "HEAD", "OPTIONS"]
    cached_methods             = ["GET", "HEAD"]
    viewer_protocol_policy     = "redirect-to-https"
    compress                   = true
    cache_policy_id            = local.caching_optimized
    response_headers_policy_id = aws_cloudfront_response_headers_policy.security.id

    dynamic "function_association" {
      for_each = aws_cloudfront_function.spa_routes
      content {
        event_type   = "viewer-request"
        function_arn = function_association.value.arn
      }
    }
  }

  # The API's paths, ahead of the static files: nothing cached, every method,
  # every viewer header but Host. One behavior carries the requests and the
  # realtime socket, which CloudFront upgrades like any other request on a
  # behavior that forwards the viewer's headers. Compression is off: the API
  # answers what it answers, and a socket's frames are never recompressed.
  # HTTPS only, since a redirect would turn a POST into a GET.
  dynamic "ordered_cache_behavior" {
    for_each = local.api_enabled ? var.api_path_patterns : []
    content {
      path_pattern             = ordered_cache_behavior.value
      target_origin_id         = local.api_origin_id
      allowed_methods          = ["DELETE", "GET", "HEAD", "OPTIONS", "PATCH", "POST", "PUT"]
      cached_methods           = ["GET", "HEAD"]
      viewer_protocol_policy   = "https-only"
      compress                 = false
      cache_policy_id          = local.caching_disabled
      origin_request_policy_id = local.all_viewer_except_host
    }
  }

  # The bucket answers a missing key with 403, since the distribution may not
  # list it; both become the site's not-found page with a 404.
  dynamic "custom_error_response" {
    for_each = var.not_found_page == "" ? [] : [403, 404]
    content {
      error_code            = custom_error_response.value
      response_code         = 404
      response_page_path    = var.not_found_page
      error_caching_min_ttl = 60
    }
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  viewer_certificate {
    acm_certificate_arn      = var.certificate_arn
    ssl_support_method       = "sni-only"
    minimum_protocol_version = "TLSv1.2_2021"
  }
}
