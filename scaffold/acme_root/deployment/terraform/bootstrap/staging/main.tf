# Staging's account, before its first deploy: everything the `account`
# module holds, the role `deploy-staging.yml` assumes, and the replication
# that carries what staging builds into production's account.
#
# scripts/cloud_create.sh applies this root, once and then again after
# production's root exists: the replication needs production's artifacts
# bucket to be there first, so the script turns it on only when it finds it.

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

module "account" {
  source    = "../../modules/account"
  providers = { aws = aws, aws.us_east_1 = aws.us_east_1 }

  environment       = "staging"
  other_environment = "production"
  state_key_prefix  = "environments/staging"

  api_domain_name  = local.staging.api_domain_name
  app_domain_name  = local.staging.app_domain_name
  site_domain_name = local.staging.site_domain_name

  sign_in_role_name = local.staging.sso_role_name

  owner_email        = var.owner_email
  monthly_budget_usd = var.monthly_budget_usd
  anomaly_monitor    = var.anomaly_monitor
}

# The one credential a merge to `main` holds: staging's, in staging's
# account. It trusts one subject, a job of this repository that declares
# `environment: staging` and runs on `main`.
module "deploy_role" {
  source = "../../modules/deploy_role"

  environment       = "staging"
  other_environment = "production"

  github_repository          = local.config.github_repository
  github_repository_id       = local.config.github_repository_id
  github_repository_owner_id = local.config.github_repository_owner_id
  github_environment         = "staging"
  github_ref                 = "refs/heads/main"
  oidc_provider_arn          = module.account.oidc_provider_arn

  state_bucket     = module.account.state_bucket
  artifacts_bucket = module.account.artifacts_bucket
  state_key_prefix = "environments/staging"

  image_repositories = module.account.repository_names
  # Staging builds, but not under this role: the build role below pushes the
  # images and keeps the static builds, and this one only reads them.
  promote_images = false

  dns_record_patterns = flatten([
    for name in [local.staging.api_domain_name, local.staging.app_domain_name] : [name, "*.${name}"]
  ])

  task_boundary_policy_arn = module.account.task_boundary_policy_arn
}

# The build credential.
#
# The jobs that build install third-party packages and run their scripts, so
# they hold a credential that can push what they built and nothing else: the
# images into the registry and the static builds under builds/ (the portal's
# and the company site's). It
# trusts one subject, the `staging-build` environment on `main`, which no job
# that applies declares, so a build step never holds the role that applies.

data "aws_iam_policy_document" "build_assume" {
  statement {
    sid     = "GitHubBuildJobsOnMain"
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
      values   = ["repo:${local.github_subject_repository}:environment:staging-build"]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:ref"
      values   = ["refs/heads/main"]
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

resource "aws_iam_role" "build" {
  name                 = "acme-build-staging"
  description          = "Pushes the images and keeps the static builds. Applies nothing."
  assume_role_policy   = data.aws_iam_policy_document.build_assume.json
  max_session_duration = 3600
}

data "aws_iam_policy_document" "build" {
  statement {
    sid       = "RegistryLogin"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }

  statement {
    sid = "PushTheImages"
    actions = [
      "ecr:BatchCheckLayerAvailability",
      "ecr:BatchGetImage",
      "ecr:CompleteLayerUpload",
      "ecr:DescribeImages",
      "ecr:GetDownloadUrlForLayer",
      "ecr:InitiateLayerUpload",
      "ecr:PutImage",
      "ecr:UploadLayerPart",
    ]
    resources = [
      for name in module.account.repository_names :
      "arn:${data.aws_partition.current.partition}:ecr:${local.config.region}:${local.staging.account_id}:repository/${name}"
    ]
  }

  statement {
    sid       = "ListTheBuilds"
    actions   = ["s3:ListBucket"]
    resources = [module.account.artifacts_bucket_arn]

    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = ["builds/*"]
    }
  }

  statement {
    sid       = "KeepTheBuilds"
    actions   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
    resources = ["${module.account.artifacts_bucket_arn}/builds/*"]
  }
}

resource "aws_iam_role_policy" "build" {
  name   = "build"
  role   = aws_iam_role.build.id
  policy = data.aws_iam_policy_document.build.json
}

# Replication.
#
# Production never reads staging's account. What it releases is a copy in its
# own: ECR copies every image staging pushes into production's registry,
# digest for digest, and S3 copies every static build staging keeps into
# production's artifacts bucket, which holds no state. Production's bootstrap root grants the two
# writes; nothing in production trusts anything else from here, and
# tearing staging down leaves production's copies where they are.

locals {
  production_artifacts_bucket_arn = "arn:${data.aws_partition.current.partition}:s3:::acme-artifacts-${local.production.account_id}"
}

resource "aws_ecr_replication_configuration" "to_production" {
  count = var.replicate_to_production ? 1 : 0

  replication_configuration {
    rule {
      destination {
        region      = local.config.region
        registry_id = local.production.account_id
      }

      repository_filter {
        filter      = "acme-"
        filter_type = "PREFIX_MATCH"
      }
    }
  }
}

data "aws_iam_policy_document" "replication_assume" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["s3.amazonaws.com"]
    }
  }
}

# Named outside `acme-staging-*`, the prefix the deploy role may write, and
# denied to it by name.
resource "aws_iam_role" "replication" {
  count = var.replicate_to_production ? 1 : 0

  name                 = "acme-replication-staging"
  description          = "Copies staging's static builds into production's artifacts bucket."
  assume_role_policy   = data.aws_iam_policy_document.replication_assume.json
  max_session_duration = 3600
}

data "aws_iam_policy_document" "replication" {
  statement {
    sid       = "ReadTheReplicationRule"
    actions   = ["s3:GetReplicationConfiguration", "s3:ListBucket"]
    resources = [module.account.artifacts_bucket_arn]
  }

  statement {
    sid = "ReadTheBuilds"
    actions = [
      "s3:GetObjectVersionAcl",
      "s3:GetObjectVersionForReplication",
      "s3:GetObjectVersionTagging",
    ]
    resources = ["${module.account.artifacts_bucket_arn}/builds/*"]
  }

  statement {
    sid = "WriteThemIntoProduction"
    actions = [
      "s3:ObjectOwnerOverrideToBucketOwner",
      "s3:ReplicateDelete",
      "s3:ReplicateObject",
      "s3:ReplicateTags",
    ]
    resources = ["${local.production_artifacts_bucket_arn}/builds/*"]
  }
}

resource "aws_iam_role_policy" "replication" {
  count = var.replicate_to_production ? 1 : 0

  name   = "replicate-portal-builds"
  role   = aws_iam_role.replication[0].id
  policy = data.aws_iam_policy_document.replication.json
}

resource "aws_s3_bucket_replication_configuration" "to_production" {
  count = var.replicate_to_production ? 1 : 0

  bucket = module.account.artifacts_bucket
  role   = aws_iam_role.replication[0].arn

  rule {
    id     = "builds"
    status = "Enabled"

    filter {
      prefix = "builds/"
    }

    delete_marker_replication {
      status = "Disabled"
    }

    destination {
      bucket  = local.production_artifacts_bucket_arn
      account = local.production.account_id

      access_control_translation {
        owner = "Destination"
      }
    }
  }
}
