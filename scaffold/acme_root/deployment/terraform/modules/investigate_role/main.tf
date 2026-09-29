# One environment's investigate role: the credential a person or an agent
# assumes to look at that environment, and a credential that can change
# nothing. An agent holding it may read every signal and every resource
# description, and may run `terraform plan -lock=false -refresh=false` on the
# environment root; what it cannot do is write to the cloud, read a tenant's
# data, open the database, or reach the other environment.
#
# Trust is a person signed in through IAM Identity Center: the account's own
# principals, narrowed by a condition to the permission set roles named in
# `operator_principal_arn_patterns`. Those roles carry a generated suffix,
# and a principal element takes no wildcard, so the pattern goes in the
# condition. A person gives an agent this role, never their own.
#
# Permission is ReadOnlyAccess, which reads the whole account, plus the calls
# ReadOnlyAccess leaves out that an investigation needs (Logs Insights
# queries, X-Ray traces, the cost explorer), and then the fences: a deny
# beats every allow, so the wide read is cut back to what the role is for.

data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}

locals {
  partition = data.aws_partition.current.partition
  account   = data.aws_caller_identity.current.account_id

  name_prefix      = "acme-${var.environment}"
  state_bucket_arn = "arn:${local.partition}:s3:::${var.state_bucket}"
}

data "aws_iam_policy_document" "assume" {
  statement {
    sid     = "OperatorsSignedInThroughIdentityCenter"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "AWS"
      identifiers = ["arn:${local.partition}:iam::${local.account}:root"]
    }

    condition {
      test     = "ArnLike"
      variable = "aws:PrincipalArn"
      values   = var.operator_principal_arn_patterns
    }
  }
}

resource "aws_iam_role" "this" {
  name                 = "acme-investigate-${var.environment}"
  description          = "Reads the ${var.environment} environment: every signal, every description, no data, no write."
  assume_role_policy   = data.aws_iam_policy_document.assume.json
  max_session_duration = 3600
}

resource "aws_iam_role_policy_attachment" "read_only" {
  role       = aws_iam_role.this.name
  policy_arn = "arn:${local.partition}:iam::aws:policy/ReadOnlyAccess"
}

# What ReadOnlyAccess does not grant and an investigation reads.

data "aws_iam_policy_document" "read" {
  statement {
    sid = "Signals"
    actions = [
      "application-autoscaling:Describe*",
      "budgets:ViewBudget",
      "ce:Get*",
      "cloudwatch:Describe*",
      "cloudwatch:Get*",
      "cloudwatch:List*",
      "ecs:Describe*",
      "ecs:List*",
      "logs:DescribeLogGroups",
      "logs:DescribeLogStreams",
      "logs:FilterLogEvents",
      "logs:GetLogEvents",
      "logs:GetQueryResults",
      "logs:StartQuery",
      "logs:StopQuery",
      "logs:StartLiveTail",
      "xray:BatchGet*",
      "xray:Get*",
    ]
    resources = ["*"]
  }

  # `terraform plan -lock=false -refresh=false` reads the state and writes
  # nothing: no lock, no plan file. The state holds no secret value: the
  # database password is written write-only and never lands in it. The
  # fences below deny every secret's value and the database login, and the
  # instance has no public address.
  statement {
    sid       = "ListOnlyThisEnvironmentsKeys"
    actions   = ["s3:ListBucket"]
    resources = [local.state_bucket_arn]

    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = ["${var.state_key_prefix}/*"]
    }
  }

  statement {
    sid       = "ReadTheState"
    actions   = ["s3:GetObject"]
    resources = ["${local.state_bucket_arn}/${var.state_key_prefix}/*"]
  }
}

resource "aws_iam_role_policy" "read" {
  name   = "read"
  role   = aws_iam_role.this.id
  policy = data.aws_iam_policy_document.read.json
}

# The fences. ReadOnlyAccess reads the whole account; these are the reads an
# investigation has no business making, and every write it could reach.

data "aws_iam_policy_document" "fences" {
  statement {
    sid       = "NoSecretValues"
    effect    = "Deny"
    actions   = ["secretsmanager:GetSecretValue"]
    resources = ["*"]
  }

  # The data plane: a tenant's files and exports, and the portal's files, of
  # either environment: S3 does not match an object call against its bucket's
  # tags, so the tag fence below does not cover the other environment's.
  # Never the state bucket, which carries no environment prefix in its name.
  statement {
    sid     = "NoObjectsInTheDataBuckets"
    effect  = "Deny"
    actions = ["s3:GetObject", "s3:GetObjectVersion"]
    resources = [
      "arn:${local.partition}:s3:::${local.name_prefix}-*/*",
      "arn:${local.partition}:s3:::acme-${var.other_environment}-*/*",
    ]
  }

  statement {
    sid       = "NoDatabaseConnections"
    effect    = "Deny"
    actions   = ["rds-db:connect"]
    resources = ["*"]
  }

  statement {
    sid       = "NothingBelongingToTheOtherEnvironment"
    effect    = "Deny"
    actions   = ["*"]
    resources = ["*"]

    condition {
      test     = "StringEquals"
      variable = "aws:ResourceTag/acme:environment"
      values   = [var.other_environment]
    }
  }

  # Every IAM write verb, and any hop to another role. A plan reads IAM
  # (iam:Get*, iam:List*), so the deny names the verbs that change something
  # rather than the whole service; IAM's verbs are a finite set and these
  # prefixes cover every one that writes.
  statement {
    sid    = "NoIamWritesAndNoRoleHopping"
    effect = "Deny"
    actions = [
      "iam:Add*",
      "iam:Attach*",
      "iam:Change*",
      "iam:Create*",
      "iam:Deactivate*",
      "iam:Delete*",
      "iam:Detach*",
      "iam:Enable*",
      "iam:PassRole",
      "iam:Put*",
      "iam:Remove*",
      "iam:Reset*",
      "iam:Resync*",
      "iam:Set*",
      "iam:Tag*",
      "iam:Untag*",
      "iam:Update*",
      "iam:Upload*",
      "sts:AssumeRole",
    ]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "fences" {
  name   = "fences"
  role   = aws_iam_role.this.id
  policy = data.aws_iam_policy_document.fences.json
}
