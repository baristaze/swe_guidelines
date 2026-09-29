# One environment's operator dashboard, the cloud twin of the Grafana
# dashboard the local profile provisions (deployment/local/grafana/dashboards/
# acme-overview.json). The first five widgets carry that dashboard's panels,
# one for one, and a unit test holds the two lists of titles equal but for
# latency; the rows after them are what only the cloud has: the database,
# the cache, the queue, and the tasks, then the reads with a latency alarm,
# the age of each queue's oldest message, and the sweep's pass duration,
# which the alarms read. The last row is both dashboards' again: the work
# queue's oldest ready item, its items failed of late, and the outbox's
# oldest pending row, which the Postgres queue alarms read.
#
# The body is dashboard.json.tftpl, a JSON document with interpolations and
# nothing else (no template loops), so the test can read it as JSON. The
# list-valued widgets receive their metric arrays as JSON fragments built
# here.
#
# The application metrics come from the `Acme` namespace the collector
# sidecar exports to, with every Prometheus label as a dimension and no
# rollups (the service module's `NoDimensionRollup`): a rollup would drop the
# environment dimension and merge the two environments' series. So CloudWatch
# has one series per label combination and no group-by. "By route" is
# therefore every combination, labelled by its values; "by status" is one sum
# per status code, listed below. The exporter adds one dimension of its own,
# `OTelLib` (the receiver's name), and a SEARCH schema names every dimension
# a series has or matches nothing, so each schema carries it.
#
# Latency is the one panel that differs. The exporter writes a histogram as
# a statistic set (count, sum, minimum, maximum), from which CloudWatch
# computes no percentile, so a per-route p95 would draw a flat zero. The
# widget reads the load balancer's TargetResponseTime p95 instead: every
# route together, and titled so.

data "aws_region" "current" {}

locals {
  running_tasks = [
    for name in var.service_names :
    ["ECS/ContainerInsights", "RunningTaskCount", "ClusterName", var.cluster_name, "ServiceName", name, { label = name }]
  ]

  # SUM over every route and method for one status code; the collector's
  # counters arrive as one-minute deltas, so the sum over a 60s period
  # divided by 60 is a rate per second.
  responses_by_status = [
    for status in var.http_statuses :
    [{
      expression = "SUM(SEARCH('{Acme,OTelLib,environment,method,route,service,status} MetricName=\"acme_http_requests_total\" environment=\"${var.environment}\" status=\"${status}\"', 'Sum', 60)) / 60"
      label      = tostring(status)
      id         = "s${status}"
    }]
  ]

  cache_cpu = [
    for id in var.cache_node_ids :
    ["AWS/ElastiCache", "EngineCPUUtilization", "CacheClusterId", id, { label = id }]
  ]

  # The reads the alarms module keeps a latency alarm on, from the metric
  # its log filter writes: raw values, so the p95 here is a true one.
  read_latency = [
    for route in var.read_latency_routes :
    ["Acme", "acme_read_latency_ms", "route", route, { label = "GET ${route}" }]
  ]

  # What the backlog alarm reads, per inbound queue; the dead-letter alarm
  # reads the "dead" lines of the queue widget above.
  queue_ages = [
    for name in var.queue_names :
    ["AWS/SQS", "ApproximateAgeOfOldestMessage", "QueueName", name, { label = name }]
  ]

  # Each metric stays an array: flatten() is recursive in Terraform and
  # would spread every row into loose strings, which CloudWatch refuses.
  queue_depths = concat(
    [for name in var.queue_names : ["AWS/SQS", "ApproximateNumberOfMessagesVisible", "QueueName", name, { label = "${name} visible" }]],
    [for name in var.queue_names : ["AWS/SQS", "ApproximateNumberOfMessagesVisible", "QueueName", "${name}-dead", { label = "${name} dead" }]],
  )
}

resource "aws_cloudwatch_dashboard" "this" {
  dashboard_name = "acme-${var.environment}"

  dashboard_body = templatefile("${path.module}/dashboard.json.tftpl", {
    environment              = var.environment
    region                   = data.aws_region.current.region
    database_identifier      = var.database_identifier
    load_balancer_arn_suffix = var.load_balancer_arn_suffix
    running_tasks            = jsonencode(local.running_tasks)
    responses_by_status      = jsonencode(local.responses_by_status)
    cache_cpu                = jsonencode(local.cache_cpu)
    queue_depths             = jsonencode(local.queue_depths)
    read_latency             = jsonencode(local.read_latency)
    queue_ages               = jsonencode(local.queue_ages)
  })
}
