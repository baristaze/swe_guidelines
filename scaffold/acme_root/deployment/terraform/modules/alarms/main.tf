# The default alarm set of one environment, and the one topic they all go
# to: the edge (5xx ratio, unhealthy targets, p95 latency), one per read
# that has a latency of its own to keep (GET /v1/me by default), the database
# (CPU, free storage), the queues (a backlog and a dead letter, per
# inbound queue), the worker's sweep (a pass longer than its interval), the
# work queue and the outbox in Postgres (a backlog, a dead letter, the
# relay's lag), and the runtime (a service running fewer tasks than it wants, one per
# service). The thresholds are inputs with defaults here, at the
# leaf, because a threshold is a number and not shape; an environment that
# wants another number passes it through the environment module, which
# exposes none of them yet.
#
# The first responder is an agent holding the investigate role: it reads the
# alarm against the platform's size before it escalates, so the topic's
# subscribers are the people who are told, not the people who act first.

locals {
  tags = { "acme:environment" = var.environment }

  # The alarm's name carries the environment so an email says which one.
  prefix = "acme-${var.environment}"

  # Both ALB metrics take the load balancer's ARN suffix as the dimension;
  # the target group metrics take both.
  load_balancer = { LoadBalancer = var.load_balancer_arn_suffix }
  target_group = {
    LoadBalancer = var.load_balancer_arn_suffix
    TargetGroup  = var.target_group_arn_suffix
  }
}

resource "aws_sns_topic" "alarms" {
  name = "${local.prefix}-alarms"
  tags = local.tags
}

# One address by declaration. Another person subscribes with
# `aws sns subscribe`; the runbook says how, and Terraform leaves it alone.
resource "aws_sns_topic_subscription" "email" {
  topic_arn = aws_sns_topic.alarms.arn
  protocol  = "email"
  endpoint  = var.alarm_email
}

# The edge.

resource "aws_cloudwatch_metric_alarm" "http_5xx_ratio" {
  alarm_name          = "${local.prefix}-http-5xx-ratio"
  alarm_description   = "More than ${var.http_5xx_ratio_percent} percent of requests answered 5xx, by the load balancer or by a target, for ${var.evaluation_periods} periods of ${var.period_seconds}s."
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = var.evaluation_periods
  threshold           = var.http_5xx_ratio_percent
  # No requests is not an outage; it is a quiet environment.
  treat_missing_data = "notBreaching"
  alarm_actions      = [aws_sns_topic.alarms.arn]
  ok_actions         = [aws_sns_topic.alarms.arn]
  tags               = local.tags

  # The load balancer publishes a 5xx count only for a minute that has one,
  # and metric math yields nothing where any input is missing, so each count
  # is filled with zero: 5xx from targets alone, or from the load balancer
  # alone, still makes a ratio.
  metric_query {
    id          = "ratio"
    expression  = "100 * (FILL(target_5xx, 0) + FILL(elb_5xx, 0)) / requests"
    label       = "5xx percent"
    return_data = true
  }

  metric_query {
    id = "requests"
    metric {
      namespace   = "AWS/ApplicationELB"
      metric_name = "RequestCount"
      period      = var.period_seconds
      stat        = "Sum"
      dimensions  = local.load_balancer
    }
  }

  metric_query {
    id = "target_5xx"
    metric {
      namespace   = "AWS/ApplicationELB"
      metric_name = "HTTPCode_Target_5XX_Count"
      period      = var.period_seconds
      stat        = "Sum"
      dimensions  = local.load_balancer
    }
  }

  metric_query {
    id = "elb_5xx"
    metric {
      namespace   = "AWS/ApplicationELB"
      metric_name = "HTTPCode_ELB_5XX_Count"
      period      = var.period_seconds
      stat        = "Sum"
      dimensions  = local.load_balancer
    }
  }
}

resource "aws_cloudwatch_metric_alarm" "unhealthy_targets" {
  alarm_name          = "${local.prefix}-unhealthy-targets"
  alarm_description   = "At least one API target failed the load balancer's health check for ${var.evaluation_periods} periods of ${var.period_seconds}s."
  namespace           = "AWS/ApplicationELB"
  metric_name         = "UnHealthyHostCount"
  dimensions          = local.target_group
  statistic           = "Maximum"
  period              = var.period_seconds
  evaluation_periods  = var.evaluation_periods
  comparison_operator = "GreaterThanOrEqualToThreshold"
  threshold           = 1
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alarms.arn]
  ok_actions          = [aws_sns_topic.alarms.arn]
  tags                = local.tags
}

resource "aws_cloudwatch_metric_alarm" "http_p95_latency" {
  alarm_name          = "${local.prefix}-http-p95-latency"
  alarm_description   = "The load balancer's p95 target response time exceeded ${var.http_p95_latency_seconds}s for ${var.evaluation_periods} periods of ${var.period_seconds}s."
  namespace           = "AWS/ApplicationELB"
  metric_name         = "TargetResponseTime"
  dimensions          = local.load_balancer
  extended_statistic  = "p95"
  period              = var.period_seconds
  evaluation_periods  = var.evaluation_periods
  comparison_operator = "GreaterThanThreshold"
  threshold           = var.http_p95_latency_seconds
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alarms.arn]
  ok_actions          = [aws_sns_topic.alarms.arn]
  tags                = local.tags
}

# One read's own latency. The load balancer's p95 is every route together,
# so a slow read that is a small share of the traffic never moves it. The
# API writes each request's route and time as fields of its access line, and
# a metric filter on its log group turns one route's lines into a metric of
# raw values, from which CloudWatch computes a true p95.

locals {
  # A route template as an alarm name can carry it: /v1/me is v1-me.
  read_latency_slugs = {
    for route, seconds in var.read_latency_p95_seconds :
    route => trim(replace(route, "/[^a-zA-Z0-9]+/", "-"), "-")
  }
}

resource "aws_cloudwatch_log_metric_filter" "read_latency" {
  for_each = var.read_latency_p95_seconds

  name           = "${local.prefix}-read-latency-${local.read_latency_slugs[each.key]}"
  log_group_name = var.api_log_group_name
  pattern        = "{ $.http.method = \"GET\" && $.http.route = \"${each.key}\" }"

  metric_transformation {
    name       = "acme_read_latency_ms"
    namespace  = "Acme"
    value      = "$.http.duration_ms"
    unit       = "Milliseconds"
    dimensions = { route = "$.http.route" }
  }
}

resource "aws_cloudwatch_metric_alarm" "read_latency" {
  for_each = var.read_latency_p95_seconds

  alarm_name          = "${local.prefix}-read-latency-${local.read_latency_slugs[each.key]}"
  alarm_description   = "GET ${each.key} answered slower than ${each.value}s at p95 for ${var.evaluation_periods} periods of ${var.period_seconds}s, as the API timed it."
  namespace           = "Acme"
  metric_name         = "acme_read_latency_ms"
  dimensions          = { route = each.key }
  extended_statistic  = "p95"
  period              = var.period_seconds
  evaluation_periods  = var.evaluation_periods
  comparison_operator = "GreaterThanThreshold"
  threshold           = each.value * 1000
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alarms.arn]
  ok_actions          = [aws_sns_topic.alarms.arn]
  tags                = local.tags

  depends_on = [aws_cloudwatch_log_metric_filter.read_latency]
}

# The database.

resource "aws_cloudwatch_metric_alarm" "database_cpu" {
  alarm_name          = "${local.prefix}-database-cpu"
  alarm_description   = "The database's CPU stayed above ${var.database_cpu_percent} percent for ${var.evaluation_periods} periods of ${var.period_seconds}s."
  namespace           = "AWS/RDS"
  metric_name         = "CPUUtilization"
  dimensions          = { DBInstanceIdentifier = var.database_identifier }
  statistic           = "Average"
  period              = var.period_seconds
  evaluation_periods  = var.evaluation_periods
  comparison_operator = "GreaterThanThreshold"
  threshold           = var.database_cpu_percent
  alarm_actions       = [aws_sns_topic.alarms.arn]
  ok_actions          = [aws_sns_topic.alarms.arn]
  tags                = local.tags
}

# Storage autoscales up to a ceiling; this fires when the free space under
# the current allocation runs low, which is early enough to read why.
resource "aws_cloudwatch_metric_alarm" "database_free_storage" {
  alarm_name          = "${local.prefix}-database-free-storage"
  alarm_description   = "The database has less than ${var.database_free_storage_bytes} bytes of free storage."
  namespace           = "AWS/RDS"
  metric_name         = "FreeStorageSpace"
  dimensions          = { DBInstanceIdentifier = var.database_identifier }
  statistic           = "Minimum"
  period              = var.period_seconds
  evaluation_periods  = var.evaluation_periods
  comparison_operator = "LessThanThreshold"
  threshold           = var.database_free_storage_bytes
  alarm_actions       = [aws_sns_topic.alarms.arn]
  ok_actions          = [aws_sns_topic.alarms.arn]
  tags                = local.tags
}

# The queues: two alarms per inbound queue. A provider's calls in are
# answered at the edge and handled by the worker from here, so a worker
# that stops draining one leaves the caller answered and nothing done.
# A backlog is the oldest message waiting past a bound, which reads the same
# at any volume; a dead letter is one message in the queue's `-dead` twin,
# where a message lands after its last attempt, and one is enough to look.
# An idle queue stops reporting after some hours, and a dead-letter queue
# that holds a message goes idle too, so missing data keeps the alarm's
# state instead of clearing it.

resource "aws_cloudwatch_metric_alarm" "queue_backlog" {
  for_each = toset(var.queue_names)

  alarm_name          = "${each.key}-backlog"
  alarm_description   = "The oldest message on ${each.key} waited longer than ${var.queue_oldest_message_seconds}s for ${var.evaluation_periods} periods of ${var.period_seconds}s: the worker is not draining it."
  namespace           = "AWS/SQS"
  metric_name         = "ApproximateAgeOfOldestMessage"
  dimensions          = { QueueName = each.key }
  statistic           = "Maximum"
  period              = var.period_seconds
  evaluation_periods  = var.evaluation_periods
  comparison_operator = "GreaterThanThreshold"
  threshold           = var.queue_oldest_message_seconds
  treat_missing_data  = "ignore"
  alarm_actions       = [aws_sns_topic.alarms.arn]
  ok_actions          = [aws_sns_topic.alarms.arn]
  tags                = local.tags
}

resource "aws_cloudwatch_metric_alarm" "queue_dead_letter" {
  for_each = toset(var.queue_names)

  alarm_name          = "${each.key}-dead-letter"
  alarm_description   = "A message is in ${each.key}-dead: it failed every attempt on ${each.key}."
  namespace           = "AWS/SQS"
  metric_name         = "ApproximateNumberOfMessagesVisible"
  dimensions          = { QueueName = "${each.key}-dead" }
  statistic           = "Maximum"
  period              = var.period_seconds
  evaluation_periods  = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  threshold           = 1
  treat_missing_data  = "ignore"
  alarm_actions       = [aws_sns_topic.alarms.arn]
  ok_actions          = [aws_sns_topic.alarms.arn]
  tags                = local.tags
}

# The sweep. A pass stops taking tenants at its budget and resumes on the
# next, so its length stays near the budget however much there is to trim.
# The worker writes each pass's duration as a field of one log line, and a
# metric filter on its log group turns those lines into a metric of raw
# values. A pass longer than the threshold means the budget is not holding:
# one step is slow on its own, and the next pass waits behind it.

resource "aws_cloudwatch_log_metric_filter" "sweep_duration" {
  name           = "${local.prefix}-sweep-duration"
  log_group_name = var.maintenance_log_group_name
  pattern        = "{ $.sweep.duration_ms = * }"

  metric_transformation {
    name      = "acme_sweep_duration_ms"
    namespace = "Acme"
    value     = "$.sweep.duration_ms"
    unit      = "Milliseconds"
  }
}

resource "aws_cloudwatch_metric_alarm" "sweep_duration" {
  alarm_name          = "${local.prefix}-sweep-duration"
  alarm_description   = "A sweep pass took longer than ${var.sweep_duration_seconds}s in each of ${var.evaluation_periods} periods of ${var.period_seconds}s, as the worker timed it: a step is slower than the pass's budget."
  namespace           = "Acme"
  metric_name         = "acme_sweep_duration_ms"
  statistic           = "Maximum"
  period              = var.period_seconds
  evaluation_periods  = var.evaluation_periods
  comparison_operator = "GreaterThanThreshold"
  threshold           = var.sweep_duration_seconds * 1000
  # A worker that is not running writes no line; the runtime alarm says so.
  treat_missing_data = "notBreaching"
  alarm_actions      = [aws_sns_topic.alarms.arn]
  ok_actions         = [aws_sns_topic.alarms.arn]
  tags               = local.tags

  depends_on = [aws_cloudwatch_log_metric_filter.sweep_duration]
}

# The work queue and the outbox. Both are Postgres tables, so nothing
# publishes a metric about them but the worker: each sweep pass reads four
# numbers across every tenant and writes them as fields of its one line, and
# a metric filter per field on the worker's log group turns them into
# metrics of raw values. Several workers each write their own line, and every
# alarm reads the largest value of the period, which is the same number read
# twice. The numbers below are the platform's, and illustrative: a busier
# platform moves them.
#
# Like the inbound queues, missing data keeps each alarm's state. A worker
# that is not running writes no line, and the runtime alarm says so; a read
# the sweep could not make leaves its field off rather than write a zero.

locals {
  # The field of the pass's line each metric is read from, prefixed with
  # `sweep.`; the metric is the field's name after `acme_`.
  sweep_gauges = {
    work_oldest_ready_seconds     = "Seconds"
    work_failed_recently          = "Count"
    outbox_oldest_pending_seconds = "Seconds"
    outbox_failed_recently        = "Count"
  }
}

resource "aws_cloudwatch_log_metric_filter" "sweep_gauge" {
  for_each = local.sweep_gauges

  name           = "${local.prefix}-sweep-${replace(each.key, "_", "-")}"
  log_group_name = var.maintenance_log_group_name
  pattern        = "{ $.sweep.${each.key} = * }"

  metric_transformation {
    name      = "acme_${each.key}"
    namespace = "Acme"
    value     = "$.sweep.${each.key}"
    unit      = each.value
  }
}

# A backlog: the work item ready longest waited past a bound. A worker with a
# free slot claims an item within seconds (it wakes on the enqueue, and polls
# every five seconds besides), and an item parked until later is not ready,
# so it never counts. Ten minutes of waiting means no worker is claiming:
# none runs, every slot is held, or no worker serves the item's lane.
resource "aws_cloudwatch_metric_alarm" "work_backlog" {
  alarm_name          = "${local.prefix}-work-backlog"
  alarm_description   = "The work item ready longest waited more than ${var.work_oldest_ready_seconds}s for a worker, in each of ${var.evaluation_periods} periods of ${var.period_seconds}s: the workers are not draining the queue."
  namespace           = "Acme"
  metric_name         = "acme_work_oldest_ready_seconds"
  statistic           = "Maximum"
  period              = var.period_seconds
  evaluation_periods  = var.evaluation_periods
  comparison_operator = "GreaterThanThreshold"
  threshold           = var.work_oldest_ready_seconds
  treat_missing_data  = "ignore"
  alarm_actions       = [aws_sns_topic.alarms.arn]
  ok_actions          = [aws_sns_topic.alarms.arn]
  tags                = local.tags

  depends_on = [aws_cloudwatch_log_metric_filter.sweep_gauge]
}

# A dead letter: an item failed, its attempts spent or its handler refused,
# and it runs again only when a person sends it back. One is enough to look,
# as on the inbound queues. The worker counts the items failed in the last
# fifteen minutes, so the alarm fires within a pass of the failure and turns
# OK a quarter of an hour after the last one, or once a person requeues it;
# the worker's `failed for good` line and the org's diary keep the record.
resource "aws_cloudwatch_metric_alarm" "work_dead_letter" {
  alarm_name          = "${local.prefix}-work-dead-letter"
  alarm_description   = "A work item failed for good in the last fifteen minutes: its attempts ran out or its handler refused it, and it runs again only when a person requeues it."
  namespace           = "Acme"
  metric_name         = "acme_work_failed_recently"
  statistic           = "Maximum"
  period              = var.period_seconds
  evaluation_periods  = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  threshold           = 1
  treat_missing_data  = "ignore"
  alarm_actions       = [aws_sns_topic.alarms.arn]
  ok_actions          = [aws_sns_topic.alarms.arn]
  tags                = local.tags

  depends_on = [aws_cloudwatch_log_metric_filter.sweep_gauge]
}

# The outbox's lag: the oldest row neither relayed nor failed. The request
# that wrote a row relays it at once, and the sweep takes what is left past a
# ten-second grace on its next pass, retrying a row that fails 30, 60, and
# 120 seconds later. A row pending for five minutes has failed at the request
# and on about four passes since: the event store or the bus refuses, and
# every push and every enqueue behind it waits.
resource "aws_cloudwatch_metric_alarm" "outbox_lag" {
  alarm_name          = "${local.prefix}-outbox-lag"
  alarm_description   = "The oldest outbox row not yet relayed landed more than ${var.outbox_oldest_pending_seconds}s ago, in each of ${var.evaluation_periods} periods of ${var.period_seconds}s: the relay is stuck."
  namespace           = "Acme"
  metric_name         = "acme_outbox_oldest_pending_seconds"
  statistic           = "Maximum"
  period              = var.period_seconds
  evaluation_periods  = var.evaluation_periods
  comparison_operator = "GreaterThanThreshold"
  threshold           = var.outbox_oldest_pending_seconds
  treat_missing_data  = "ignore"
  alarm_actions       = [aws_sns_topic.alarms.arn]
  ok_actions          = [aws_sns_topic.alarms.arn]
  tags                = local.tags

  depends_on = [aws_cloudwatch_log_metric_filter.sweep_gauge]
}

# The outbox's dead letter: a row whose relay attempts ran out. It is no
# longer pending, so the lag above no longer sees it, and the change it
# announced never reached the stream or the queue: a push nobody received,
# or work that never started. One is enough to look, as on the work queue.
# The worker counts the rows failed for good in the last fifteen minutes, so
# the alarm fires within a pass of the failure and turns OK a quarter of an
# hour after the last one; the worker's `failed for good` line and the org's
# diary keep the record.
resource "aws_cloudwatch_metric_alarm" "outbox_dead_letter" {
  alarm_name          = "${local.prefix}-outbox-dead-letter"
  alarm_description   = "An outbox row failed for good in the last fifteen minutes: its relay attempts ran out, and the change it announced never reached the event stream or the work queue."
  namespace           = "Acme"
  metric_name         = "acme_outbox_failed_recently"
  statistic           = "Maximum"
  period              = var.period_seconds
  evaluation_periods  = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  threshold           = 1
  treat_missing_data  = "ignore"
  alarm_actions       = [aws_sns_topic.alarms.arn]
  ok_actions          = [aws_sns_topic.alarms.arn]
  tags                = local.tags

  depends_on = [aws_cloudwatch_log_metric_filter.sweep_gauge]
}

# The runtime: one alarm per service, on the gap between what it wants and
# what runs. Container Insights publishes both counts per service; the gap
# is a metric math expression so a scale-out (desired rises, running follows)
# reads the same as a deploy. Its window is longer than the others: the worker
# rolls one task at a time with no second one beside it, so a routine deploy
# leaves it at none for its drain plus a start, a few minutes that are not an
# outage.
resource "aws_cloudwatch_metric_alarm" "tasks_below_desired" {
  for_each = toset(var.service_names)

  alarm_name          = "${local.prefix}-${each.key}-tasks-below-desired"
  alarm_description   = "The ${each.key} service ran fewer tasks than it wants for ${var.tasks_below_desired_periods} periods of ${var.period_seconds}s."
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = var.tasks_below_desired_periods
  threshold           = 0
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alarms.arn]
  ok_actions          = [aws_sns_topic.alarms.arn]
  tags                = local.tags

  metric_query {
    id          = "gap"
    expression  = "desired - running"
    label       = "tasks missing"
    return_data = true
  }

  metric_query {
    id = "desired"
    metric {
      namespace   = "ECS/ContainerInsights"
      metric_name = "DesiredTaskCount"
      period      = var.period_seconds
      stat        = "Maximum"
      dimensions  = { ClusterName = var.cluster_name, ServiceName = each.key }
    }
  }

  metric_query {
    id = "running"
    metric {
      namespace   = "ECS/ContainerInsights"
      metric_name = "RunningTaskCount"
      period      = var.period_seconds
      stat        = "Minimum"
      dimensions  = { ClusterName = var.cluster_name, ServiceName = each.key }
    }
  }
}
