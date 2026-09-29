# One inbound queue per enum member, each with its own dead-letter queue.
# The impl resolves the dead-letter queue by the name suffix "-dead" that the
# redrive policy's target ARN ends with.

locals {
  tags = { "acme:environment" = var.environment }
}

resource "aws_sqs_queue" "dead" {
  for_each = toset(var.queues)

  name                      = "${var.prefix}${each.key}-dead"
  message_retention_seconds = 1209600
  sqs_managed_sse_enabled   = true
  tags                      = local.tags
}

resource "aws_sqs_queue" "this" {
  for_each = toset(var.queues)

  name                       = "${var.prefix}${each.key}"
  visibility_timeout_seconds = var.visibility_timeout_seconds
  message_retention_seconds  = var.retention_seconds
  receive_wait_time_seconds  = 20
  sqs_managed_sse_enabled    = true
  tags                       = local.tags

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.dead[each.key].arn
    maxReceiveCount     = var.max_receives
  })
}

data "aws_iam_policy_document" "use" {
  statement {
    actions = [
      "sqs:SendMessage",
      "sqs:ReceiveMessage",
      "sqs:DeleteMessage",
      "sqs:ChangeMessageVisibility",
      "sqs:GetQueueUrl",
      "sqs:GetQueueAttributes",
    ]
    resources = concat(
      [for queue in aws_sqs_queue.this : queue.arn],
      [for queue in aws_sqs_queue.dead : queue.arn],
    )
  }
}

resource "aws_iam_policy" "use" {
  name   = "acme-${var.environment}-queues"
  policy = data.aws_iam_policy_document.use.json
  tags   = local.tags
}
