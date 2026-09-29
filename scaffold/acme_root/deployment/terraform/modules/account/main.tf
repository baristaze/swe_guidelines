# What every environment's account holds before its first deploy: the image
# registry, the state bucket, the artifacts bucket, GitHub's OIDC provider, the ceiling on every
# task role, the role an operator reads the environment under, the budget,
# and the two hosted zones its public names live in.
#
# Each environment has an account of its own, so none of this is shared
# between them. The account is the fence: a staging credential is a
# credential in another account, and nothing in production trusts it. The
# two bootstrap roots call this module, add the roles their own deploy
# workflow assumes, and wire the one direction anything crosses: staging's
# images and static builds replicate into production.

data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}
data "aws_region" "current" {}

locals {
  partition = data.aws_partition.current.partition
  account   = data.aws_caller_identity.current.account_id
  region    = data.aws_region.current.region

  # The account id makes the name unique across AWS without an input.
  state_bucket = "acme-state-${local.account}"

  # A person signs in through IAM Identity Center; the permission set's role
  # sits under this path with a generated suffix, so it is matched by
  # pattern and never by a name someone copies in.
  operator_principal_arn_patterns = length(var.operator_principal_arn_patterns) > 0 ? var.operator_principal_arn_patterns : [
    "arn:${local.partition}:iam::${local.account}:role/aws-reserved/sso.amazonaws.com/*AWSReservedSSO_${var.sign_in_role_name}_*",
  ]
}

# Registry.

resource "aws_ecr_repository" "this" {
  for_each = toset(var.images)

  name                 = each.key
  image_tag_mutability = "IMMUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  encryption_configuration {
    encryption_type = "AES256"
  }
}

resource "aws_ecr_lifecycle_policy" "this" {
  for_each = aws_ecr_repository.this

  repository = each.value.name
  # Production runs whatever it was last released on, however many merges
  # ago, so the count alone would expire it. The production deploy tags each
  # digest it promotes `prod-<sha>`, and an image a rule of higher priority
  # selects is never expired by a lower one: the last ten releases stay, for
  # the tasks production replaces and for a rollback. In staging's account
  # no image carries the prefix and the first rule selects nothing.
  policy = jsonencode({
    rules = [
      {
        rulePriority = 1
        description  = "keep the last 10 images production ran"
        selection = {
          tagStatus     = "tagged"
          tagPrefixList = ["prod-"]
          countType     = "imageCountMoreThan"
          countNumber   = 10
        }
        action = { type = "expire" }
      },
      {
        rulePriority = 2
        description  = "keep the last 30 images"
        selection = {
          tagStatus   = "any"
          countType   = "imageCountMoreThan"
          countNumber = 30
        }
        action = { type = "expire" }
      },
    ]
  })
}

# State.

resource "aws_s3_bucket" "state" {
  bucket = local.state_bucket
}

# Versioning keeps every earlier state, and replication requires it on both
# ends.
resource "aws_s3_bucket_versioning" "state" {
  bucket = aws_s3_bucket.state.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_public_access_block" "state" {
  bucket                  = aws_s3_bucket.state.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# Artifacts.
#
# What a build keeps by commit, the portal build under builds/portal/<sha>/
# and the company site's under builds/site/<sha>/, lives here and never
# beside the state. Staging's copy replicates into production's, so the one
# write staging's account may make in production's lands in a bucket that
# holds no state.

resource "aws_s3_bucket" "artifacts" {
  bucket = "acme-artifacts-${local.account}"
}

resource "aws_s3_bucket_versioning" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_public_access_block" "artifacts" {
  bucket                  = aws_s3_bucket.artifacts.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# A build is kept a year, long enough to redeploy any release a rollback
# would reach for, and an overwritten or deleted version thirty days. The
# registry's lifecycle is the images' counterpart.
resource "aws_s3_bucket_lifecycle_configuration" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id

  rule {
    id     = "builds"
    status = "Enabled"

    filter {
      prefix = "builds/"
    }

    expiration {
      days = 365
    }

    noncurrent_version_expiration {
      noncurrent_days = 30
    }
  }
}

# Audit.
#
# One trail per account, every region, management events, with log file
# validation: who did what to the cloud itself, which no application signal
# records. The first trail of management events is free; its bucket holds
# nothing else, and no deploy role is granted any call on either.

resource "aws_s3_bucket" "audit" {
  bucket = "acme-audit-${local.account}"
}

resource "aws_s3_bucket_public_access_block" "audit" {
  bucket                  = aws_s3_bucket.audit.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_lifecycle_configuration" "audit" {
  bucket = aws_s3_bucket.audit.id

  rule {
    id     = "trail"
    status = "Enabled"

    filter {}

    expiration {
      days = var.audit_retention_days
    }
  }
}

data "aws_iam_policy_document" "audit" {
  statement {
    sid       = "TheTrailChecksTheBucket"
    actions   = ["s3:GetBucketAcl"]
    resources = [aws_s3_bucket.audit.arn]

    principals {
      type        = "Service"
      identifiers = ["cloudtrail.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:SourceArn"
      values   = ["arn:${local.partition}:cloudtrail:${local.region}:${local.account}:trail/acme-${var.environment}"]
    }
  }

  statement {
    sid       = "TheTrailWritesItsLogs"
    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.audit.arn}/AWSLogs/${local.account}/*"]

    principals {
      type        = "Service"
      identifiers = ["cloudtrail.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "s3:x-amz-acl"
      values   = ["bucket-owner-full-control"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:SourceArn"
      values   = ["arn:${local.partition}:cloudtrail:${local.region}:${local.account}:trail/acme-${var.environment}"]
    }
  }
}

resource "aws_s3_bucket_policy" "audit" {
  bucket = aws_s3_bucket.audit.id
  policy = data.aws_iam_policy_document.audit.json
}

resource "aws_cloudtrail" "this" {
  name                          = "acme-${var.environment}"
  s3_bucket_name                = aws_s3_bucket.audit.id
  is_multi_region_trail         = true
  include_global_service_events = true
  enable_log_file_validation    = true

  depends_on = [aws_s3_bucket_policy.audit]
}

# Credentials.
#
# One GitHub OIDC provider per account. The roles behind it are the
# bootstrap root's: which subjects an account trusts is what makes it
# staging's or production's.

resource "aws_iam_openid_connect_provider" "github" {
  url            = "https://token.actions.githubusercontent.com"
  client_id_list = ["sts.amazonaws.com"]
  thumbprint_list = [
    "6938fd4d98bab03faadb97b34396831e3780aea1",
    "1c58a3a8518e8759bf075b76b750d4f2df264fcd",
  ]
}

# The ceiling on every role a deploy run creates. A task role is made by the
# graph, which the deploy role applies, so without a boundary the deploy role
# could mint a role wider than itself and hand it to a task. This is what the
# `NoRoleWithoutTheTaskBoundary` fence requires be attached.

data "aws_iam_policy_document" "task_boundary" {
  statement {
    sid     = "ItsOwnBucketsAndQueues"
    actions = ["s3:*", "sqs:*"]
    resources = [
      "arn:${local.partition}:s3:::acme-${var.environment}-*",
      "arn:${local.partition}:s3:::acme-${var.environment}-*/*",
      "arn:${local.partition}:sqs:*:${local.account}:acme-${var.environment}-*",
    ]
  }

  statement {
    sid       = "ItsOwnSecrets"
    actions   = ["secretsmanager:GetSecretValue", "secretsmanager:DescribeSecret"]
    resources = ["arn:${local.partition}:secretsmanager:*:${local.account}:secret:acme/${var.environment}/*"]
  }

  # A tenant's own secrets, which the application writes through its secrets
  # capability: a Slack install's bot token, put, renewed, and deleted. The
  # graph gives the writes to the serving tasks on this part of the prefix
  # alone; this is the ceiling that lets it.
  statement {
    sid = "ItsTenantsSecrets"
    actions = [
      "secretsmanager:CreateSecret",
      "secretsmanager:PutSecretValue",
      "secretsmanager:DeleteSecret",
    ]
    resources = ["arn:${local.partition}:secretsmanager:*:${local.account}:secret:acme/${var.environment}/app/org/*"]
  }

  # The one secret write a task may hold: the grant task minting an operator
  # token into one of the two token secrets. The graph gives it to that task
  # alone; this is the ceiling that lets it.
  statement {
    sid     = "ItsOwnOperatorTokens"
    actions = ["secretsmanager:PutSecretValue"]
    resources = [
      "arn:${local.partition}:secretsmanager:*:${local.account}:secret:acme-${var.environment}-provisioner-token-??????",
      "arn:${local.partition}:secretsmanager:*:${local.account}:secret:acme-${var.environment}-smoke-token-??????",
    ]
  }

  statement {
    sid = "ItsOwnLogStreams"
    actions = [
      "logs:CreateLogStream",
      "logs:DescribeLogGroups",
      "logs:DescribeLogStreams",
      "logs:PutLogEvents",
    ]
    resources = ["arn:${local.partition}:logs:*:${local.account}:log-group:/acme/${var.environment}/*"]
  }

  statement {
    sid       = "PullTheImageItRuns"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }

  statement {
    sid = "TheImagesOfThisPlatform"
    actions = [
      "ecr:BatchCheckLayerAvailability",
      "ecr:BatchGetImage",
      "ecr:GetDownloadUrlForLayer",
    ]
    resources = [for repository in aws_ecr_repository.this : repository.arn]
  }

  # What the collector sidecar emits.
  statement {
    sid = "Telemetry"
    actions = [
      "xray:GetSamplingRules",
      "xray:GetSamplingTargets",
      "xray:PutTelemetryRecords",
      "xray:PutTraceSegments",
    ]
    resources = ["*"]
  }

  statement {
    sid       = "Metrics"
    actions   = ["cloudwatch:PutMetricData"]
    resources = ["*"]

    condition {
      test     = "StringEquals"
      variable = "cloudwatch:namespace"
      values   = ["Acme"]
    }
  }
}

resource "aws_iam_policy" "task_boundary" {
  # Deliberately not "acme-<environment>-...": that shape is what a deploy
  # role may write, and a boundary it could rewrite is no boundary.
  name        = "acme-task-boundary-${var.environment}"
  description = "The ceiling on every role the ${var.environment} deploy role creates."
  policy      = data.aws_iam_policy_document.task_boundary.json
}

# The operators. A person signs in through IAM Identity Center and assumes
# the investigate role from there; an agent runs under the same profile. No
# IAM user and no access key: every credential here is short-lived.

module "investigate_role" {
  source = "../investigate_role"

  environment       = var.environment
  other_environment = var.other_environment

  state_bucket     = aws_s3_bucket.state.bucket
  state_key_prefix = var.state_key_prefix

  operator_principal_arn_patterns = local.operator_principal_arn_patterns
}

# Cost.
#
# The budget is the catch-all every other bound sits under: the owner hears
# at half of it, at most of it, at all of it, and when the forecast crosses
# it. The anomaly monitor watches each service's spend beside it and reports
# a jump the budget would only show at month's end. One account, one
# environment, so the account's bill is the environment's. The monitor needs
# Cost Explorer, which the organization turns on; until it does, the
# bootstrap script leaves the monitor out and says so.

resource "aws_budgets_budget" "monthly" {
  name         = "acme-${var.environment}-monthly"
  budget_type  = "COST"
  limit_amount = tostring(var.monthly_budget_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  dynamic "notification" {
    for_each = [
      { type = "ACTUAL", threshold = 50 },
      { type = "ACTUAL", threshold = 80 },
      { type = "ACTUAL", threshold = 100 },
      { type = "FORECASTED", threshold = 100 },
    ]

    content {
      comparison_operator        = "GREATER_THAN"
      threshold                  = notification.value.threshold
      threshold_type             = "PERCENTAGE"
      notification_type          = notification.value.type
      subscriber_email_addresses = [var.owner_email]
    }
  }
}

resource "aws_ce_anomaly_monitor" "services" {
  count = var.anomaly_monitor ? 1 : 0

  name              = "acme-${var.environment}-services"
  monitor_type      = "DIMENSIONAL"
  monitor_dimension = "SERVICE"
}

resource "aws_ce_anomaly_subscription" "owner" {
  count = var.anomaly_monitor ? 1 : 0

  name             = "acme-${var.environment}-anomalies"
  frequency        = "DAILY"
  monitor_arn_list = [aws_ce_anomaly_monitor.services[0].arn]

  subscriber {
    type    = "EMAIL"
    address = var.owner_email
  }

  # Below this an anomaly is noise on a budget this size.
  threshold_expression {
    dimension {
      key           = "ANOMALY_TOTAL_IMPACT_ABSOLUTE"
      match_options = ["GREATER_THAN_OR_EQUAL"]
      values        = [tostring(var.anomaly_impact_usd)]
    }
  }
}

# The zones.
#
# The domain is registered at Cloudflare, and a domain Cloudflare registers
# keeps Cloudflare's name servers, so the domain's own zone stays there. Each
# public name is delegated to a zone of its own here instead: the name's
# records sit at that zone's apex, and the zone belongs to one account. The
# bootstrap script writes each zone's name servers into Cloudflare as NS
# records, once.

resource "aws_route53_zone" "this" {
  for_each = toset([var.api_domain_name, var.app_domain_name])

  name    = each.key
  comment = "Acme ${var.environment}: ${each.key}, delegated from the domain's zone at Cloudflare"
}

# The company site's certificate.
#
# The site's name is not delegated: it is the domain's apex in production and
# staging.acme.example in staging, and neither can be. The apex is Cloudflare's
# own zone apex, and a delegation of staging.acme.example would hide the
# app.staging and api.staging delegations beneath it. So the name is a record
# in the Cloudflare zone, and so is its certificate's validation record,
# which only the create run can write: it holds the Cloudflare token, and no
# deploy does. The certificate is therefore made here, in the root the
# create run applies; the run writes the validation record and waits for the
# certificate to be issued, and the environment root finds it by its name.

resource "aws_acm_certificate" "site" {
  provider = aws.us_east_1

  domain_name       = var.site_domain_name
  validation_method = "DNS"
  tags              = { "acme:environment" = var.environment }

  lifecycle {
    create_before_destroy = true
  }
}

# Service-linked roles. A fresh account has none, and ECS, RDS, ElastiCache,
# the load balancer, and ECS autoscaling each need theirs before the first
# resource of that kind. Creating one is an IAM write, which the deployer
# never holds, so the bootstrap makes them, once per account.
resource "aws_iam_service_linked_role" "this" {
  for_each = toset([
    "ecs.amazonaws.com",
    "ecs.application-autoscaling.amazonaws.com",
    "elasticache.amazonaws.com",
    "elasticloadbalancing.amazonaws.com",
    "rds.amazonaws.com",
  ])

  aws_service_name = each.key
}
