# Three kinds of secret live under one environment. The platform's own
# credentials (the four database URLs, the TOTP encryption key, the edge
# secret, the Sentry DSN, the WorkOS application key and webhook secret) are
# declared here and injected into tasks by the execution role.
# Application-managed secrets, the ones SecretsInterface reads at runtime,
# live under "<prefix>app/", which is the value of ACME_SECRETS_NAME_PREFIX,
# so a process can never reach its own bootstrap credentials through the
# capability; that grant is read-only, as the task boundary in the account
# module is. The two operator tokens are the third kind: declared empty here,
# written by the grant task alone, and named outside the prefix, so no
# grant on the prefix reaches them.

data "aws_partition" "current" {}
data "aws_region" "current" {}
data "aws_caller_identity" "current" {}

locals {
  tags               = { "acme:environment" = var.environment }
  application_prefix = "${var.prefix}app/"
  # A deleted secret keeps its name for the recovery window, so a nuke
  # followed by a create within it would be refused. Outside production the
  # window is none at all; production keeps thirty days, and the nuke's apply
  # (destroyable) lifts it on the way down.
  recovery_window_in_days = var.destroyable || var.environment != "production" ? 0 : 30
  secret_arn_prefix       = "arn:${data.aws_partition.current.partition}:secretsmanager:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:secret:"
}

# The database's four URLs, one per login. The master's is the secret the
# first release wrote under `database_url`, kept under that name: the master
# password exists for the run that set it and nowhere else, so a new secret
# could hold it only through a rotation. The migrate task alone reads it.
moved {
  from = aws_secretsmanager_secret.database_url
  to   = aws_secretsmanager_secret.database_master_url
}

moved {
  from = aws_secretsmanager_secret_version.database_url
  to   = aws_secretsmanager_secret_version.database_master_url
}

resource "aws_secretsmanager_secret" "database_master_url" {
  name                    = "${var.prefix}database_url"
  recovery_window_in_days = local.recovery_window_in_days
  tags                    = local.tags
}

# Write-only: the URL carries the password, and neither the state nor a plan
# may hold it. The version is the database module's password version, so a
# rotation writes both.
resource "aws_secretsmanager_secret_version" "database_master_url" {
  secret_id = aws_secretsmanager_secret.database_master_url.id
  # `ssl=require`: the connection is encrypted, never left to what the
  # driver and the server happen to agree on.
  secret_string_wo         = "postgresql+asyncpg://${var.database_username}:${urlencode(var.database_password)}@${var.database_address}:${var.database_port}/${var.database_name}?ssl=require"
  secret_string_wo_version = var.database_password_version
}

# The three logins the application connects as. Each password is generated
# for the run and written once per password version, write-only, into its
# URL; the migrate task's `ensure-logins` reads the URLs and sets each
# login's password from its own, so the secret is the one place a password
# is decided. Raising the version rotates the three with the master's.
locals {
  logins = {
    runtime   = "acme_runtime"
    system    = "acme_system"
    migration = "acme_migration"
  }
}

ephemeral "random_password" "login" {
  for_each = local.logins

  length  = 32
  special = false
}

resource "aws_secretsmanager_secret" "login_url" {
  for_each = local.logins

  name                    = "${var.prefix}database_${each.key}_url"
  recovery_window_in_days = local.recovery_window_in_days
  tags                    = local.tags
}

resource "aws_secretsmanager_secret_version" "login_url" {
  for_each = local.logins

  secret_id                = aws_secretsmanager_secret.login_url[each.key].id
  secret_string_wo         = "postgresql+asyncpg://${each.value}:${urlencode(ephemeral.random_password.login[each.key].result)}@${var.database_address}:${var.database_port}/${var.database_name}?ssl=require"
  secret_string_wo_version = var.database_password_version
}

# The key the API encrypts each person's TOTP secret under: 32 random bytes,
# URL-safe base64 (a Fernet key). Written once and never again: a new key
# would leave every enrolled secret unreadable, so a rotation is a change of
# its own that re-encrypts, not a number raised here.
ephemeral "random_password" "totp_encryption_key" {
  length  = 32
  special = false
}

resource "aws_secretsmanager_secret" "totp_encryption_key" {
  name                    = "${var.prefix}totp_encryption_key"
  recovery_window_in_days = local.recovery_window_in_days
  tags                    = local.tags
}

resource "aws_secretsmanager_secret_version" "totp_encryption_key" {
  secret_id                = aws_secretsmanager_secret.totp_encryption_key.id
  secret_string_wo         = replace(replace(base64encode(ephemeral.random_password.totp_encryption_key.result), "+", "-"), "/", "_")
  secret_string_wo_version = 1
}

# The value the portal's CloudFront distribution adds to every request it
# sends the API, as the X-Acme-Edge header. It lets the API trust one more
# hop of X-Forwarded-For: CloudFront appends the viewer's address, the load
# balancer appends CloudFront's, and only a request that carries this value
# came through this distribution and not through anyone else's. It is not
# ephemeral: the distribution's configuration holds it in the clear, so the
# state holds it too. Raising edge_secret_version writes a new one to both in
# one apply. Until the API's tasks roll, a request through the portal is
# counted by the edge's address: fairness lost for a minute, no door opened.
resource "random_password" "edge" {
  length  = 48
  special = false

  keepers = {
    version = var.edge_secret_version
  }
}

resource "aws_secretsmanager_secret" "edge_secret" {
  name                    = "${var.prefix}edge_secret"
  recovery_window_in_days = local.recovery_window_in_days
  tags                    = local.tags
}

resource "aws_secretsmanager_secret_version" "edge_secret" {
  secret_id                = aws_secretsmanager_secret.edge_secret.id
  secret_string_wo         = random_password.edge.result
  secret_string_wo_version = var.edge_secret_version
}

# The Sentry-compatible DSN errors report to (sentry.io or a hosted GlitchTip).
# There is one tracker project for the product, and every environment holds the
# same project's DSN here: what separates the events is the environment each
# process sends on every event, not the project it sends to. The secret is per
# environment because a secret is per account and production's account cannot
# read staging's, so the one DSN is written into each account once.
# Terraform creates it as "off", which leaves reporting off (Secrets Manager
# refuses an empty value), and never writes it again: set the real value once with
#   aws secretsmanager put-secret-value --secret-id <prefix>sentry_dsn --secret-string <dsn>
resource "aws_secretsmanager_secret" "sentry_dsn" {
  name                    = "${var.prefix}sentry_dsn"
  recovery_window_in_days = local.recovery_window_in_days
  tags                    = local.tags
}

resource "aws_secretsmanager_secret_version" "sentry_dsn" {
  secret_id     = aws_secretsmanager_secret.sentry_dsn.id
  secret_string = "off"

  lifecycle {
    ignore_changes = [secret_string]
  }
}

# The Acme App application's API key: the client secret the API exchanges
# sign-in codes with, and the key it sends invitations with. A process
# credential, injected into the API and the worker as ACME_WORKOS_API_KEY
# (the worker deletes a deleted account's person with it), and named
# outside the application prefix so no process reaches it through the
# secrets capability. Each environment holds a key of the Acme App in its
# own WorkOS environment (staging's, production's), made on that
# application's API keys tab, never the environment's; the API refuses to
# start on any other key. Terraform
# creates it as "off", which the API reads as not configured (it starts, and
# every sign-in through WorkOS answers 503 until the key is set), and never
# writes it again: set the real value once with
#   aws secretsmanager put-secret-value --secret-id <prefix>workos_api_key --secret-string <key>
# and roll the API and the worker so their tasks start with it.
resource "aws_secretsmanager_secret" "workos_api_key" {
  name                    = "${var.prefix}workos_api_key"
  recovery_window_in_days = local.recovery_window_in_days
  tags                    = local.tags
}

resource "aws_secretsmanager_secret_version" "workos_api_key" {
  secret_id     = aws_secretsmanager_secret.workos_api_key.id
  secret_string = "off"

  lifecycle {
    ignore_changes = [secret_string]
  }
}

# The secret WorkOS signs each webhook delivery with, which the API checks
# at /webhooks/identity before it queues the delivery for the worker. A
# process credential of the API alone, injected as ACME_WORKOS_WEBHOOK_SECRET
# and named outside the application prefix, like the API key. Terraform
# creates it as "off", which leaves the route refusing every delivery until
# the secret is set, and never writes it again: set the real
# value once, from the endpoint's page in the WorkOS dashboard, with
#   aws secretsmanager put-secret-value --secret-id <prefix>workos_webhook_secret --secret-string <secret>
# and roll the API so its tasks start with it.
resource "aws_secretsmanager_secret" "workos_webhook_secret" {
  name                    = "${var.prefix}workos_webhook_secret"
  recovery_window_in_days = local.recovery_window_in_days
  tags                    = local.tags
}

resource "aws_secretsmanager_secret_version" "workos_webhook_secret" {
  secret_id     = aws_secretsmanager_secret.workos_webhook_secret.id
  secret_string = "off"

  lifecycle {
    ignore_changes = [secret_string]
  }
}

data "aws_iam_policy_document" "application" {
  statement {
    actions = [
      "secretsmanager:GetSecretValue",
      "secretsmanager:DescribeSecret",
    ]
    resources = ["${local.secret_arn_prefix}${local.application_prefix}*"]
  }

  # A tenant's own secrets, each under org/<org_id>/ in the prefix: the one
  # write a serving process holds. A credential an org connects is created
  # there, replaced when it renews, and deleted when the org disconnects it.
  statement {
    actions = [
      "secretsmanager:CreateSecret",
      "secretsmanager:PutSecretValue",
      "secretsmanager:DeleteSecret",
    ]
    resources = ["${local.secret_arn_prefix}${local.application_prefix}org/*"]
  }
}

resource "aws_iam_policy" "application" {
  name   = "acme-${var.environment}-secrets"
  policy = data.aws_iam_policy_document.application.json
  tags   = local.tags
}

# The operator tokens the grant task mints: the provisioner's (write) and the
# smoke job's (read). Declared with no value; `acme-api grant-operator
# --mint-token` writes each, and nothing else can: the grant task's role is
# the one role given the policy below, and the deploy role reads them.
resource "aws_secretsmanager_secret" "operator_token" {
  for_each = toset(["provisioner", "smoke"])

  name                    = "acme-${var.environment}-${each.key}-token"
  recovery_window_in_days = local.recovery_window_in_days
  tags                    = local.tags
}

data "aws_iam_policy_document" "operator_tokens" {
  statement {
    actions   = ["secretsmanager:PutSecretValue"]
    resources = [for secret in aws_secretsmanager_secret.operator_token : secret.arn]
  }
}

resource "aws_iam_policy" "operator_tokens" {
  name   = "acme-${var.environment}-operator-tokens"
  policy = data.aws_iam_policy_document.operator_tokens.json
  tags   = local.tags
}
