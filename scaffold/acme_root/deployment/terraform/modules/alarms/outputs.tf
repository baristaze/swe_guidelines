output "topic_arn" {
  description = "Where every alarm goes; another address subscribes to it by hand."
  value       = aws_sns_topic.alarms.arn
}

output "alarm_names" {
  description = "Every alarm the environment declares, for a reader that lists their states."
  value = concat(
    [
      aws_cloudwatch_metric_alarm.http_5xx_ratio.alarm_name,
      aws_cloudwatch_metric_alarm.unhealthy_targets.alarm_name,
      aws_cloudwatch_metric_alarm.http_p95_latency.alarm_name,
      aws_cloudwatch_metric_alarm.database_cpu.alarm_name,
      aws_cloudwatch_metric_alarm.database_free_storage.alarm_name,
      aws_cloudwatch_metric_alarm.sweep_duration.alarm_name,
      aws_cloudwatch_metric_alarm.work_backlog.alarm_name,
      aws_cloudwatch_metric_alarm.work_dead_letter.alarm_name,
      aws_cloudwatch_metric_alarm.outbox_lag.alarm_name,
      aws_cloudwatch_metric_alarm.outbox_dead_letter.alarm_name,
    ],
    [for alarm in aws_cloudwatch_metric_alarm.read_latency : alarm.alarm_name],
    [for alarm in aws_cloudwatch_metric_alarm.queue_backlog : alarm.alarm_name],
    [for alarm in aws_cloudwatch_metric_alarm.queue_dead_letter : alarm.alarm_name],
    [for alarm in aws_cloudwatch_metric_alarm.tasks_below_desired : alarm.alarm_name],
  )
}

output "read_latency_routes" {
  description = "The GET routes with a latency alarm of their own, for the dashboard that draws them."
  value       = keys(var.read_latency_p95_seconds)
}
