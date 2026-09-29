# Production's account, before its first release: everything the `account`
# module holds, the two roles `deploy-production.yml` assumes, and the two
# grants that let staging's replication write its copies in.
#
# Two roles, because the approval gates the credential and not only the
# step:
#
#   environment:production-plan -> acme-plan-production      reads and plans
#   environment:production      -> acme-deploy-production    applies
#
# A GitHub job presents `repo:<owner>@<owner id>/<name>@<repo id>:environment:<name>` only when it
# declares that environment, so the subject condition on each role is what
# binds it to the gate: the `production` environment holds a required
# reviewer, and a job that has not passed the reviewer never produces the
# subject the apply role trusts. The plan that the reviewer reads is made
# under `acme-plan-production`, which can read production and write its own
# lock and plan file, and can change nothing.

locals {
  config = jsondecode(file("${path.module}/../../../cloud/environments.json"))
  # The repository issues GitHub's immutable OIDC subject, which carries
  # the owner's and the repository's ids beside their names.
  github_subject_repository = format(
    "%s@%s/%s@%s",
    split("/", local.config.github_repository)[0], local.config.github_repository_owner_id,
    split("/", local.config.github_repository)[1], local.config.github_repository_id,
  )
  staging    = local.config.environments.staging
  production = local.config.environments.production
}

data "aws_partition" "current" {}

locals {
  partition = data.aws_partition.current.partition
  account   = local.production.account_id
}

module "account" {
  source    = "../../modules/account"
  providers = { aws = aws, aws.us_east_1 = aws.us_east_1 }

  environment       = "production"
  other_environment = "staging"
  state_key_prefix  = "environments/prod"

  api_domain_name  = local.production.api_domain_name
  app_domain_name  = local.production.app_domain_name
  site_domain_name = local.production.site_domain_name

  sign_in_role_name = local.production.sso_role_name

  owner_email        = var.owner_email
  monthly_budget_usd = var.monthly_budget_usd
  anomaly_monitor    = var.anomaly_monitor
}

module "deploy_role" {
  source = "../../modules/deploy_role"

  environment       = "production"
  other_environment = "staging"

  github_repository          = local.config.github_repository
  github_repository_id       = local.config.github_repository_id
  github_repository_owner_id = local.config.github_repository_owner_id
  github_environment         = "production"
  github_ref                 = "refs/heads/release"
  oidc_provider_arn          = module.account.oidc_provider_arn

  state_bucket     = module.account.state_bucket
  artifacts_bucket = module.account.artifacts_bucket
  state_key_prefix = "environments/prod"

  image_repositories = module.account.repository_names
  promote_images     = true

  dns_record_patterns = flatten([
    for name in [local.production.api_domain_name, local.production.app_domain_name] : [name, "*.${name}"]
  ])

  task_boundary_policy_arn = module.account.task_boundary_policy_arn
}

# The credential the jobs before the approval run under. It reads production
# and writes three things: the state lock Terraform takes while it plans, the
# plan file the reviewer approves, and nothing else. The state holds no
# secret value, because the database password is written write-only; a
# refresh still reads production's secrets through the secret store, which
# this role is granted for that. What it cannot do is change production.
# That is the line the approval is there to hold.

data "aws_iam_policy_document" "plan_assume" {
  statement {
    sid     = "GitHubJobsInTheProductionPlanEnvironment"
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [module.account.oidc_provider_arn]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:sub"
      values   = ["repo:${local.github_subject_repository}:environment:production-plan"]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:ref"
      values   = ["refs/heads/release"]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:repository_id"
      values   = [local.config.github_repository_id]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:repository_owner_id"
      values   = [local.config.github_repository_owner_id]
    }
  }
}

resource "aws_iam_role" "plan" {
  name                 = "acme-plan-production"
  description          = "Resolves the images and plans production, before the approval. Changes nothing."
  assume_role_policy   = data.aws_iam_policy_document.plan_assume.json
  max_session_duration = 3600
}

resource "aws_iam_role_policy_attachment" "plan_read" {
  role       = aws_iam_role.plan.name
  policy_arn = "arn:${local.partition}:iam::aws:policy/ReadOnlyAccess"
}

data "aws_iam_policy_document" "plan" {
  statement {
    sid       = "ListOnlyProductionsKeys"
    actions   = ["s3:ListBucket"]
    resources = [module.account.state_bucket_arn]

    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = ["environments/prod/*", "plans/environments/prod/*"]
    }
  }

  # Terraform takes the lock to plan and drops it again; the state beside it
  # is read, never written, because a plan does not persist state.
  statement {
    sid       = "TheStateLockWhileItPlans"
    actions   = ["s3:PutObject", "s3:DeleteObject"]
    resources = ["${module.account.state_bucket_arn}/environments/prod/*.tflock"]
  }

  statement {
    sid       = "TheSavedPlanTheReviewerApproves"
    actions   = ["s3:PutObject"]
    resources = ["${module.account.state_bucket_arn}/plans/environments/prod/*"]
  }

  statement {
    sid       = "TheSecretsARefreshReads"
    actions   = ["secretsmanager:GetSecretValue"]
    resources = ["arn:${local.partition}:secretsmanager:*:${local.account}:secret:acme/production/*"]
  }
}

resource "aws_iam_role_policy" "plan" {
  name   = "plan"
  role   = aws_iam_role.plan.id
  policy = data.aws_iam_policy_document.plan.json
}

data "aws_iam_policy_document" "plan_fences" {
  # Nothing tagged staging lives in this account; the fence holds if a root
  # is ever applied in the wrong one.
  statement {
    sid       = "NothingBelongingToStaging"
    effect    = "Deny"
    actions   = ["*"]
    resources = ["*"]

    condition {
      test     = "StringEquals"
      variable = "aws:ResourceTag/acme:environment"
      values   = ["staging"]
    }
  }

  statement {
    sid     = "NoWideningOfTheDeployCredentials"
    effect  = "Deny"
    actions = ["iam:*", "sts:AssumeRole"]
    resources = [
      "arn:${local.partition}:iam::${local.account}:role/acme-deploy-*",
      "arn:${local.partition}:iam::${local.account}:role/acme-plan-*",
      "arn:${local.partition}:iam::${local.account}:role/acme-investigate-*",
      "arn:${local.partition}:iam::${local.account}:role/acme-replication-*",
      "arn:${local.partition}:iam::${local.account}:role/acme-build-*",
      "arn:${local.partition}:iam::${local.account}:policy/acme-task-boundary-*",
    ]
  }
}

resource "aws_iam_role_policy" "plan_fences" {
  name   = "fences"
  role   = aws_iam_role.plan.id
  policy = data.aws_iam_policy_document.plan_fences.json
}

# The two writes staging's account may make here, and only these: an image
# into a repository this root made, and a static build under builds/
# of the artifacts bucket, which holds no state.
# Neither lets staging read anything in production, and replication never
# creates a repository, so every one keeps the settings declared above.

data "aws_iam_policy_document" "registry" {
  statement {
    sid     = "StagingReplicatesItsImagesIn"
    actions = ["ecr:ReplicateImage"]
    resources = [
      for name in module.account.repository_names :
      "arn:${local.partition}:ecr:${local.config.region}:${local.account}:repository/${name}"
    ]

    principals {
      type        = "AWS"
      identifiers = ["arn:${local.partition}:iam::${local.staging.account_id}:root"]
    }
  }
}

resource "aws_ecr_registry_policy" "this" {
  policy = data.aws_iam_policy_document.registry.json
}

# The principal is staging's account and the condition names its replication
# role: a bucket policy that names a role directly is refused until the role
# exists, and staging's root makes it only after this bucket does.
data "aws_iam_policy_document" "artifacts_bucket" {
  statement {
    sid = "StagingReplicatesItsBuildsIn"
    actions = [
      "s3:ObjectOwnerOverrideToBucketOwner",
      "s3:ReplicateDelete",
      "s3:ReplicateObject",
      "s3:ReplicateTags",
    ]
    resources = ["${module.account.artifacts_bucket_arn}/builds/*"]

    principals {
      type        = "AWS"
      identifiers = ["arn:${local.partition}:iam::${local.staging.account_id}:root"]
    }

    condition {
      test     = "ArnEquals"
      variable = "aws:PrincipalArn"
      values   = ["arn:${local.partition}:iam::${local.staging.account_id}:role/acme-replication-staging"]
    }
  }

  statement {
    sid       = "StagingReadsTheVersioningItReplicatesInto"
    actions   = ["s3:GetBucketVersioning", "s3:PutBucketVersioning"]
    resources = [module.account.artifacts_bucket_arn]

    principals {
      type        = "AWS"
      identifiers = ["arn:${local.partition}:iam::${local.staging.account_id}:root"]
    }

    condition {
      test     = "ArnEquals"
      variable = "aws:PrincipalArn"
      values   = ["arn:${local.partition}:iam::${local.staging.account_id}:role/acme-replication-staging"]
    }
  }

  # Only replication writes a static build here. A direct write or delete
  # under the prefix is refused to every principal, the administrator
  # included, so a released build is never replaced in place. A second
  # version replicated from a changed source still lands; the digest staging
  # recorded is what catches it, before any plan.
  statement {
    sid       = "NoDirectWriteToAKeptBuild"
    effect    = "Deny"
    actions   = ["s3:PutObject", "s3:DeleteObject", "s3:DeleteObjectVersion"]
    resources = ["${module.account.artifacts_bucket_arn}/builds/*"]

    principals {
      type        = "*"
      identifiers = ["*"]
    }
  }
}

resource "aws_s3_bucket_policy" "artifacts" {
  bucket = module.account.artifacts_bucket
  policy = data.aws_iam_policy_document.artifacts_bucket.json
}
