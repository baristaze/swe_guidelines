# The API's public surface: an application load balancer at the API's domain
# name. HTTPS serves the API and its realtime WebSocket; HTTP redirects.

locals {
  tags = { "acme:environment" = var.environment }
  # The idle timeout is pinned beside the realtime ping interval in one shared
  # file, which the api and the portal test against; the load balancer reads
  # the same value, so a change there reaches the infrastructure.
  realtime_timeouts = jsondecode(file("${path.module}/../../../realtime-timeouts.json"))
}

resource "aws_lb" "this" {
  name                       = "acme-${var.environment}"
  load_balancer_type         = "application"
  subnets                    = var.subnet_ids
  security_groups            = var.security_group_ids
  drop_invalid_header_fields = true
  idle_timeout               = local.realtime_timeouts.load_balancer_idle_timeout_seconds
  tags                       = local.tags
}

resource "aws_lb_target_group" "api" {
  name        = "acme-${var.environment}-api"
  port        = var.target_port
  protocol    = "HTTP"
  target_type = "ip"
  vpc_id      = var.vpc_id
  tags        = local.tags

  # How long a deregistered target keeps its open connections before ECS
  # stops its task. The load balancer stops sending it new requests within a
  # few seconds, and a request already on it ends within its deadline, 20
  # seconds (ACME_REQUEST_DEADLINE_SECONDS), which its calls to providers
  # share; the database's statement timeout (10 seconds) bounds each wait on
  # storage. After the delay the process gets SIGTERM and still finishes what
  # it holds, inside the task's 30-second stop timeout. A realtime socket still open
  # on the old task closes when the delay ends, and the client reconnects to
  # the new one. The pings and the idle timeout in realtime-timeouts.json
  # keep a living socket open; they ask nothing of a draining one, so they
  # hold as they are. Every second here is a second of every rollout.
  deregistration_delay = 15

  # The probe is /healthz, liveness, which reads no dependency and answers
  # in about a millisecond. The readiness deadline
  # (ACME_READINESS_TIMEOUT_SECONDS, 2.0) is shorter than the timeout too,
  # so either answer arrives inside the poll.
  health_check {
    path    = var.health_check_path
    matcher = "200"
    # A new target serves after two passing checks: 20 seconds from the
    # process's first answer. Each load balancer node probes on its own, so
    # this is a few requests a second at most, all to a handler that reads
    # nothing.
    interval          = 10
    timeout           = 5
    healthy_threshold = 2
    # Three misses in a row, 30 seconds of silence, take a target out of the
    # rotation. A slow request never trips it: the probe reads nothing, so
    # only a stalled or dead process misses three.
    unhealthy_threshold = 3
  }
}

resource "aws_lb_listener" "https" {
  load_balancer_arn = aws_lb.this.arn
  port              = 443
  protocol          = "HTTPS"
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  certificate_arn   = var.certificate_arn
  tags              = local.tags

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.api.arn
  }
}

resource "aws_lb_listener" "http_redirect" {
  load_balancer_arn = aws_lb.this.arn
  port              = 80
  protocol          = "HTTP"
  tags              = local.tags

  default_action {
    type = "redirect"

    redirect {
      port        = "443"
      protocol    = "HTTPS"
      status_code = "HTTP_301"
    }
  }
}

# /metrics is for the collector sidecar, which scrapes it over localhost inside
# the task; the internet gets a 404 before the request reaches a target.
resource "aws_lb_listener_rule" "hide_metrics" {
  listener_arn = aws_lb_listener.https.arn
  priority     = 1
  tags         = local.tags

  condition {
    path_pattern {
      values = ["/metrics", "/metrics/*"]
    }
  }

  action {
    type = "fixed-response"

    fixed_response {
      content_type = "text/plain"
      message_body = "Not Found"
      status_code  = "404"
    }
  }
}
