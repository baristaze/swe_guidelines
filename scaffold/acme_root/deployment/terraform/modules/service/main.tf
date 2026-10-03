# One application process: a task definition around one container image and
# a service that keeps `desired_count` of it running. Workers and services
# share this shape; a worker has no port and no target group.
#
# Beside the process runs an OpenTelemetry collector (ADOT). It scrapes the
# process's /metrics over localhost into CloudWatch metrics (EMF, namespace
# Acme) and forwards the OTLP traces the process sends to localhost:4318 on
# to X-Ray. It is not essential: a collector failure never stops the process.

data "aws_region" "current" {}
data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}

locals {
  tags = {
    "acme:service"     = var.name
    "acme:environment" = var.environment
  }

  # Application Auto Scaling names a service by cluster name, not ARN.
  cluster_name = element(split("/", var.cluster_arn), 1)

  # The ceiling `shared` declares for every role a deploy run creates. The
  # deploy role is refused a CreateRole that does not carry it, so a
  # compromised deploy run cannot mint a task role wider than itself.
  permissions_boundary = "arn:${data.aws_partition.current.partition}:iam::${data.aws_caller_identity.current.account_id}:policy/acme-task-boundary-${var.environment}"

  collector_name = "otel-collector"

  collector_config = {
    extensions = { health_check = { endpoint = "0.0.0.0:13133" } }
    receivers = {
      otlp = { protocols = { http = { endpoint = "127.0.0.1:4318" } } }
      prometheus = {
        config = {
          scrape_configs = [{
            job_name        = var.name
            scrape_interval = "30s"
            static_configs = [{
              targets = ["localhost:${var.metrics_port}"]
              labels  = { service = var.name, environment = var.environment }
            }]
          }]
        }
      }
    }
    processors = {
      batch = { timeout = "10s" }
      # X-Ray filters only on an annotation whose key is letters, digits, and
      # underscores, and the exporter keeps the dot of `acme.request_id`, so
      # the span carries the id again under a key X-Ray can filter on.
      "attributes/request_id" = {
        actions = [{ key = "acme_request_id", from_attribute = "acme.request_id", action = "insert" }]
      }
    }
    exporters = {
      awsemf = {
        namespace               = "Acme"
        log_group_name          = aws_cloudwatch_log_group.metrics.name
        log_stream_name         = "{TaskId}"
        dimension_rollup_option = "NoDimensionRollup"
      }
      # The request id is the annotation `acme_request_id`, the one filter
      # by id X-Ray has (ops/src/acme/ops/signals/cloud.py).
      awsxray = { indexed_attributes = ["acme_request_id"] }
    }
    service = {
      extensions = ["health_check"]
      pipelines = {
        metrics = { receivers = ["prometheus"], processors = ["batch"], exporters = ["awsemf"] }
        traces  = { receivers = ["otlp"], processors = ["attributes/request_id", "batch"], exporters = ["awsxray"] }
      }
    }
  }

  collector = {
    name              = local.collector_name
    image             = var.collector_image
    essential         = false
    memoryReservation = 96
    environment       = [{ name = "AOT_CONFIG_CONTENT", value = yamlencode(local.collector_config) }]
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.this.name
        "awslogs-region"        = data.aws_region.current.region
        "awslogs-stream-prefix" = local.collector_name
      }
    }
    # The image is FROM scratch; this binary probes the health_check extension.
    healthCheck = {
      command     = ["CMD", "/healthcheck"]
      interval    = 30
      timeout     = 5
      retries     = 3
      startPeriod = 10
    }
  }

  container = merge(
    {
      name        = var.name
      image       = var.image
      essential   = true
      stopTimeout = var.stop_timeout_seconds
      dependsOn   = [{ containerName = local.collector_name, condition = "START" }]
      environment = [
        for key, value in merge(var.environment_variables, {
          ACME_OTEL_ENDPOINT = "http://127.0.0.1:4318"
        }) : { name = key, value = value }
      ]
      secrets = [for key, arn in var.secrets : { name = key, valueFrom = arn }]
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
    var.port == null ? {} : { portMappings = [{ containerPort = var.port, protocol = "tcp" }] },
    var.health_check_command == null ? {} : {
      healthCheck = {
        command     = var.health_check_command
        interval    = 10
        timeout     = 5
        retries     = 3
        startPeriod = 30
      }
    },
  )
}

resource "aws_cloudwatch_log_group" "this" {
  name              = "/acme/${var.environment}/${var.name}"
  retention_in_days = var.log_retention_days
  tags              = local.tags
}

# The collector writes metrics as embedded-metric-format log events here, and
# CloudWatch extracts them into the Acme namespace.
resource "aws_cloudwatch_log_group" "metrics" {
  name              = "/acme/${var.environment}/${var.name}/metrics"
  retention_in_days = var.log_retention_days
  tags              = local.tags
}

# Roles: the execution role pulls the image, writes logs, and reads the
# injected secrets; the task role is what the process itself runs as.

data "aws_iam_policy_document" "assume_ecs_tasks" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

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

# The execution role serves every revision of the task definition, so it
# also reads what the revision before this one injects: a rollout the
# circuit breaker rolls back starts that revision's tasks again.
data "aws_iam_policy_document" "execution_secrets" {
  count = length(var.secrets) > 0 ? 1 : 0

  statement {
    actions   = ["secretsmanager:GetSecretValue"]
    resources = distinct(concat(values(var.secrets), var.rollback_secret_arns))
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

# What the collector sidecar needs, on the task role it shares with the process.
data "aws_iam_policy_document" "telemetry" {
  statement {
    actions = [
      "logs:CreateLogStream",
      "logs:PutLogEvents",
      "logs:DescribeLogStreams",
      "logs:DescribeLogGroups",
    ]
    resources = [aws_cloudwatch_log_group.metrics.arn, "${aws_cloudwatch_log_group.metrics.arn}:*"]
  }

  statement {
    actions = [
      "xray:PutTraceSegments",
      "xray:PutTelemetryRecords",
      "xray:GetSamplingRules",
      "xray:GetSamplingTargets",
      "xray:GetSamplingStatisticSummaries",
    ]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "telemetry" {
  name   = "telemetry"
  role   = aws_iam_role.task.id
  policy = data.aws_iam_policy_document.telemetry.json
}

resource "aws_ecs_task_definition" "this" {
  family                   = "acme-${var.environment}-${var.name}"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.cpu
  memory                   = var.memory
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn
  container_definitions    = jsonencode([local.container, local.collector])
  tags                     = local.tags

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64"
  }
}

# What runs before the service rolls: for the API, the migration. The
# commands run in order, in one one-off task on the task definition
# `pre_rollout` names (the migrate task's, whose credentials no serving task
# holds), started from the machine that applies with the same credentials.
# One task, not one per command: each task start is a Fargate cold start,
# about a minute. The service depends on it, so a command that fails ends the
# apply with the old tasks still serving.
#
# It runs when its triggers change. A caller that names `triggers` decides
# what calls for a run (the API: the migration files, the database, the
# passwords); one that names none runs it on every new revision of either
# task definition. The commands are always a trigger. A run that fails
# leaves the resource tainted, so the next apply runs it again. A service
# that waits for another's pre-rollout run passes its `rollout_gate` output
# as `rollout_after`.
resource "terraform_data" "pre_rollout" {
  count = var.pre_rollout == null ? 0 : 1

  triggers_replace = [
    var.pre_rollout.triggers == null ? {
      task_definition        = aws_ecs_task_definition.this.arn
      pre_rollout_definition = var.pre_rollout.task_definition_arn
    } : var.pre_rollout.triggers,
    var.pre_rollout.commands,
  ]

  provisioner "local-exec" {
    command = "${path.module}/pre_rollout.sh"
    environment = {
      AWS_REGION      = data.aws_region.current.region
      CLUSTER         = var.cluster_arn
      TASK_DEFINITION = var.pre_rollout.task_definition_arn
      SUBNETS         = join(",", var.subnet_ids)
      SECURITY_GROUPS = join(",", var.security_group_ids)
      CONTAINER       = var.pre_rollout.container
      COMMANDS        = jsonencode(var.pre_rollout.commands)
    }
  }
}

resource "terraform_data" "rollout_after" {
  input = var.rollout_after
}

# `desired_count` is not in `ignore_changes`, on purpose. With autoscaling
# on, an apply sets the count back to the floor and the policy raises it
# again within its cooldown while load is there; a deploy is already a roll,
# and a few minutes at the floor is the price. What it buys is that the
# root's number stays the truth: a change to desired_count applies, with the
# switch on or off, and nothing in the state disagrees with the file.
resource "aws_ecs_service" "this" {
  name            = var.name
  cluster         = var.cluster_arn
  task_definition = aws_ecs_task_definition.this.arn
  desired_count   = var.desired_count
  launch_type     = "FARGATE"
  propagate_tags  = "SERVICE"
  tags            = local.tags

  # The apply ends when the new tasks serve and the old ones have stopped; a
  # rollout the circuit breaker rolls back fails the apply instead of leaving
  # a green job over old tasks. The wait is what ECS takes, no more: its tail
  # is the load balancer's drain and the old task's stop, both set short.
  wait_for_steady_state = true

  deployment_maximum_percent         = var.deployment_maximum_percent
  deployment_minimum_healthy_percent = var.deployment_minimum_healthy_percent

  # How long ECS ignores the load balancer's verdict on a new task, counted
  # from the task's start. A task on a fresh Fargate host takes 60 to 90
  # seconds to answer (the host, the image pulled and unpacked, the process's
  # imports; 58, 62, 83, and 87 seconds on four starts in a row), and the
  # target then needs two passing checks, 20 more seconds. 150 covers the
  # slowest start with 40 seconds to spare. A grace shorter than the start
  # replaces a healthy task before it answers, and the rollout starts over.
  # It costs a healthy task nothing: ECS moves on at the second passing
  # check. Only a task that runs and never answers is replaced later.
  health_check_grace_period_seconds = var.target_group_arn == null ? null : 150

  depends_on = [terraform_data.pre_rollout, terraform_data.rollout_after]

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  network_configuration {
    subnets          = var.subnet_ids
    security_groups  = var.security_group_ids
    assign_public_ip = false
  }

  dynamic "load_balancer" {
    for_each = var.target_group_arn == null ? [] : [var.target_group_arn]

    content {
      target_group_arn = load_balancer.value
      container_name   = var.name
      container_port   = var.port
    }
  }
}

# Scale-out as a lever. Declared with the service, off by default at the
# root: the floor is the desired count, so turning it on changes nothing
# until load does, and the ceiling is the number an environment names. The
# metric is the service's average CPU, which Fargate publishes with no
# collector in the way.

resource "aws_appautoscaling_target" "this" {
  count = var.autoscaling.enabled ? 1 : 0

  service_namespace  = "ecs"
  scalable_dimension = "ecs:service:DesiredCount"
  resource_id        = "service/${local.cluster_name}/${aws_ecs_service.this.name}"
  min_capacity       = var.desired_count
  max_capacity       = var.autoscaling.max
  tags               = local.tags
}

resource "aws_appautoscaling_policy" "cpu" {
  count = var.autoscaling.enabled ? 1 : 0

  name               = "acme-${var.environment}-${var.name}-cpu"
  policy_type        = "TargetTrackingScaling"
  service_namespace  = aws_appautoscaling_target.this[0].service_namespace
  scalable_dimension = aws_appautoscaling_target.this[0].scalable_dimension
  resource_id        = aws_appautoscaling_target.this[0].resource_id

  target_tracking_scaling_policy_configuration {
    target_value = var.autoscaling.target_cpu
    # Out fast, in slowly: a burst is answered in a minute, and a lull has
    # to last five before a task is taken away.
    scale_out_cooldown = 60
    scale_in_cooldown  = 300

    predefined_metric_specification {
      predefined_metric_type = "ECSServiceAverageCPUUtilization"
    }
  }
}
