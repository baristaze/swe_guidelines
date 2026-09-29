# The dashboard body is a shape CloudWatch accepts: every widget's metrics
# is a list of rows, and every row is itself an array (a metric's names, or
# an expression object inside one). A recursive flatten once spread the queue
# rows into loose strings, and PutDashboard refused the whole body. Runs
# offline: `terraform test` in this folder.

mock_provider "aws" {
  mock_data "aws_region" {
    defaults = { region = "us-east-1" }
  }
}

variables {
  environment              = "test"
  cluster_name             = "acme-test"
  service_names            = ["api", "maintenance"]
  database_identifier      = "acme-test"
  load_balancer_arn_suffix = "app/acme-test/0123456789abcdef"
  cache_node_ids           = ["acme-test-001", "acme-test-002"]
  queue_names              = ["acme-test-work", "acme-test-events"]
}

run "every_metric_row_is_an_array" {
  command = plan

  assert {
    condition = alltrue([
      for widget in jsondecode(aws_cloudwatch_dashboard.this.dashboard_body).widgets :
      alltrue([for row in try(widget.properties.metrics, []) : can(row[0])])
    ])
    error_message = "A widget's metrics holds a row that is not an array."
  }

  assert {
    condition = length([
      for widget in jsondecode(aws_cloudwatch_dashboard.this.dashboard_body).widgets :
      widget if length(try(widget.properties.metrics, [])) == 4 && can(regex("SQS", jsonencode(widget.properties.metrics)))
    ]) == 1
    error_message = "The queue widget shows each queue and its dead-letter queue, one row each."
  }

  # The exporter adds `OTelLib` to every series, and a SEARCH schema that
  # leaves out a dimension the series has matches nothing. The namespace is
  # in double quotes, which a name with a space needs; the body is JSON, so
  # each quote reads as an escaped one.
  assert {
    condition = alltrue([
      for schema in regexall("SEARCH\\('\\{[^}]*\\}", aws_cloudwatch_dashboard.this.dashboard_body) :
      startswith(schema, "SEARCH('{\\\"Acme\\\",OTelLib,")
    ]) && length(regexall("SEARCH\\('\\{", aws_cloudwatch_dashboard.this.dashboard_body)) > 0
    error_message = "Every SEARCH names the Acme namespace in double quotes and the OTelLib dimension."
  }
}
