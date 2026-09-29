# One one-off task: a task definition that no service keeps running, started
# by `aws ecs run-task` when there is a job to do and stopped when it ends.
# The migration and the operator grant are the two. Each is its own
# definition, with its own roles, so the credentials it carries are its own:
# the migrate task holds the master's and the migration login's URLs, which
# no serving task does, and the grant task holds the one write on the token
# secrets, which nothing else does.
#
# No collector sidecar: a one-off task serves no metrics and sends no
# traces, and the task's exit code is its container's alone.

data "aws_region" "current" {}
data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}

locals {
  tags = {
    "acme:task"        = var.name
    "acme:environment" = var.environment
  }

  # The ceiling the account's bootstrap declares for every role a deploy run
  # creates (modules/service says why).
  permissions_boundary = "arn:${data.aws_partition.current.partition}:iam::${data.aws_caller_identity.current.account_id}:policy/acme-task-boundary-${var.environment}"

  container = merge(
    {
      name        = var.name
      image       = var.image
      essential   = true
      environment = [for key, value in var.environment_variables : { name = key, value = value }]
      secrets     = [for key, arn in var.secrets : { name = key, valueFrom = arn }]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.this.name
          "awslogs-region"        = data.aws_region.current.region
          "awslogs-stream-prefix" = var.name
        }
      }
    },
    var.command == null ? {} : { command = var.command },
  )
}

resource "aws_cloudwatch_log_group" "this" {
  name              = "/acme/${var.environment}/${var.name}"
  retention_in_days = var.log_retention_days
  tags              = local.tags
}

data "aws_iam_policy_document" "assume_ecs_tasks" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

# The execution role pulls the image, writes the log, and reads the injected
# secrets; the task role is what the process runs as.

resource "aws_iam_role" "execution" {
  name                 = "acme-${var.environment}-${var.name}-execution"
  assume_role_policy   = data.aws_iam_policy_document.assume_ecs_tasks.json
  permissions_boundary = local.permissions_boundary
  tags                 = local.tags
}

resource "aws_iam_role_policy_attachment" "execution" {
  role       = aws_iam_role.execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

data "aws_iam_policy_document" "execution_secrets" {
  count = length(var.secrets) > 0 ? 1 : 0

  statement {
    actions   = ["secretsmanager:GetSecretValue"]
    resources = values(var.secrets)
  }
}

resource "aws_iam_role_policy" "execution_secrets" {
  count = length(var.secrets) > 0 ? 1 : 0

  name   = "injected-secrets"
  role   = aws_iam_role.execution.id
  policy = data.aws_iam_policy_document.execution_secrets[0].json
}

resource "aws_iam_role" "task" {
  name                 = "acme-${var.environment}-${var.name}-task"
  assume_role_policy   = data.aws_iam_policy_document.assume_ecs_tasks.json
  permissions_boundary = local.permissions_boundary
  tags                 = local.tags
}

resource "aws_iam_role_policy_attachment" "task" {
  count = length(var.policy_arns)

  role       = aws_iam_role.task.name
  policy_arn = var.policy_arns[count.index]
}

resource "aws_ecs_task_definition" "this" {
  family                   = "acme-${var.environment}-${var.name}"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.cpu
  memory                   = var.memory
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn
  container_definitions    = jsonencode([local.container])
  tags                     = local.tags

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64"
  }
}
