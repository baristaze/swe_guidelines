# The work queue's and the outbox's alarms read the metrics the worker's
# sweep line is filtered into, from the worker's own log group, and keep
# their state through missing data. Runs offline: `terraform test` in this
# folder.

mock_provider "aws" {}

variables {
  environment                = "test"
  alarm_email                = "alarms@example.test"
  load_balancer_arn_suffix   = "app/acme-test/0123456789abcdef"
  target_group_arn_suffix    = "targetgroup/acme-test-api/0123456789abcdef"
  database_identifier        = "acme-test"
  cluster_name               = "acme-test"
  api_log_group_name         = "/acme/test/api"
  maintenance_log_group_name = "/acme/test/maintenance"
  queue_names                = ["acme-test-webhooks"]
  service_names              = ["api", "maintenance"]
}

run "each_alarm_reads_a_field_of_the_sweep_line" {
  command = plan

  assert {
    condition = alltrue([
      for field, filter in aws_cloudwatch_log_metric_filter.sweep_gauge :
      filter.log_group_name == "/acme/test/maintenance"
      && filter.pattern == "{ $.sweep.${field} = * }"
      && filter.metric_transformation[0].name == "acme_${field}"
      && filter.metric_transformation[0].namespace == "Acme"
      && filter.metric_transformation[0].value == "$.sweep.${field}"
    ])
    error_message = "A filter does not turn its field of the worker's sweep line into its metric."
  }

  assert {
    condition = toset([
      for alarm in [
        aws_cloudwatch_metric_alarm.work_backlog,
        aws_cloudwatch_metric_alarm.work_dead_letter,
        aws_cloudwatch_metric_alarm.outbox_lag,
        aws_cloudwatch_metric_alarm.outbox_dead_letter,
      ] : alarm.metric_name
      ]) == toset([
      for filter in aws_cloudwatch_log_metric_filter.sweep_gauge : filter.metric_transformation[0].name
    ])
    error_message = "Each alarm reads one of the metrics the filters write, and every one is read."
  }

  assert {
    condition = alltrue([
      for alarm in [
        aws_cloudwatch_metric_alarm.work_backlog,
        aws_cloudwatch_metric_alarm.work_dead_letter,
        aws_cloudwatch_metric_alarm.outbox_lag,
        aws_cloudwatch_metric_alarm.outbox_dead_letter,
      ] :
      alarm.namespace == "Acme" && alarm.statistic == "Maximum"
      && alarm.treat_missing_data == "ignore"
      && length(alarm.alarm_actions) == 1 && length(alarm.ok_actions) == 1
    ])
    error_message = "An alarm does not read the largest value, keep its state through missing data, or go to the topic."
  }
}

run "the_thresholds_are_the_defaults" {
  command = plan

  assert {
    condition = (
      aws_cloudwatch_metric_alarm.work_backlog.threshold == 600
      && aws_cloudwatch_metric_alarm.work_backlog.comparison_operator == "GreaterThanThreshold"
      && aws_cloudwatch_metric_alarm.work_backlog.evaluation_periods == 3
    )
    error_message = "The work backlog alarm fires on the oldest ready item past ten minutes, for three periods."
  }

  assert {
    condition = (
      aws_cloudwatch_metric_alarm.work_dead_letter.threshold == 1
      && aws_cloudwatch_metric_alarm.work_dead_letter.comparison_operator == "GreaterThanOrEqualToThreshold"
      && aws_cloudwatch_metric_alarm.work_dead_letter.evaluation_periods == 1
    )
    error_message = "The work dead-letter alarm fires on one failed item, in one period."
  }

  assert {
    condition = (
      aws_cloudwatch_metric_alarm.outbox_lag.threshold == 300
      && aws_cloudwatch_metric_alarm.outbox_lag.comparison_operator == "GreaterThanThreshold"
      && aws_cloudwatch_metric_alarm.outbox_lag.evaluation_periods == 3
    )
    error_message = "The outbox lag alarm fires on the oldest pending row past five minutes, for three periods."
  }

  assert {
    condition = (
      aws_cloudwatch_metric_alarm.outbox_dead_letter.threshold == 1
      && aws_cloudwatch_metric_alarm.outbox_dead_letter.comparison_operator == "GreaterThanOrEqualToThreshold"
      && aws_cloudwatch_metric_alarm.outbox_dead_letter.evaluation_periods == 1
      && aws_cloudwatch_metric_alarm.outbox_dead_letter.alarm_name == "acme-test-outbox-dead-letter"
    )
    error_message = "The outbox dead-letter alarm fires on one row failed for good, in one period."
  }

  assert {
    condition     = length(output.alarm_names) == 15
    error_message = "The environment declares fifteen alarms, the work backlog, the work dead letter, the outbox lag, and the outbox dead letter among them."
  }
}

run "an_environment_passes_another_number" {
  command = plan

  variables {
    work_oldest_ready_seconds     = 120
    outbox_oldest_pending_seconds = 60
  }

  assert {
    condition = (
      aws_cloudwatch_metric_alarm.work_backlog.threshold == 120
      && aws_cloudwatch_metric_alarm.outbox_lag.threshold == 60
    )
    error_message = "The thresholds are inputs."
  }
}
