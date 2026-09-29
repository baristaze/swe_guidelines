# Each URL output waits for its version: a task (the migration first) reads
# the value, and one started before the version exists finds no AWSCURRENT
# and fails the first apply.

output "database_url_secret_arn" {
  description = "The runtime login's URL: ACME_DATABASE_URL in every task."
  value       = aws_secretsmanager_secret.login_url["runtime"].arn
  depends_on  = [aws_secretsmanager_secret_version.login_url]
}

output "database_system_url_secret_arn" {
  description = "The system login's URL: ACME_DATABASE_SYSTEM_URL in every task."
  value       = aws_secretsmanager_secret.login_url["system"].arn
  depends_on  = [aws_secretsmanager_secret_version.login_url]
}

output "database_migration_url_secret_arn" {
  description = "The migration login's URL: ACME_DATABASE_MIGRATION_URL, in the migrate task only."
  value       = aws_secretsmanager_secret.login_url["migration"].arn
  depends_on  = [aws_secretsmanager_secret_version.login_url]
}

output "database_master_url_secret_arn" {
  description = "The master user's URL: ACME_DATABASE_MASTER_URL, in the migrate task only."
  value       = aws_secretsmanager_secret.database_master_url.arn
  depends_on  = [aws_secretsmanager_secret_version.database_master_url]
}

output "totp_encryption_key_secret_arn" {
  description = "Injected into the API as ACME_TOTP_ENCRYPTION_KEY."
  value       = aws_secretsmanager_secret.totp_encryption_key.arn
  depends_on  = [aws_secretsmanager_secret_version.totp_encryption_key]
}

output "edge_secret_arn" {
  description = "Injected into the API as ACME_EDGE_SECRET."
  value       = aws_secretsmanager_secret.edge_secret.arn
  depends_on  = [aws_secretsmanager_secret_version.edge_secret]
}

output "edge_secret" {
  description = "The same value, for the portal's distribution to send as its X-Acme-Edge origin header."
  value       = random_password.edge.result
  sensitive   = true
}

output "application_prefix" {
  description = "ACME_SECRETS_NAME_PREFIX for every process."
  value       = local.application_prefix
}

output "policy_arn" {
  description = "Attached to every task role; reads the application prefix, and writes a tenant's own secrets under its org/ part alone."
  value       = aws_iam_policy.application.arn
}

output "sentry_dsn_secret_arn" {
  description = "Injected into every task as ACME_SENTRY_DSN; the product's one project, the same DSN in every environment, and \"off\" until set, which leaves reporting off."
  value       = aws_secretsmanager_secret.sentry_dsn.arn
  depends_on  = [aws_secretsmanager_secret_version.sentry_dsn]
}

output "workos_api_key_secret_arn" {
  description = "The Acme App application's API key, injected into the API and the worker as ACME_WORKOS_API_KEY; \"off\" until set, which leaves sign-in through WorkOS answering 503."
  value       = aws_secretsmanager_secret.workos_api_key.arn
  depends_on  = [aws_secretsmanager_secret_version.workos_api_key]
}

output "slack_client_secret_arn" {
  description = "Injected into the API and the worker as ACME_SLACK_CLIENT_SECRET; \"off\" until set, which leaves Slack unconfigured."
  value       = aws_secretsmanager_secret.slack_app["slack_client_secret"].arn
  depends_on  = [aws_secretsmanager_secret_version.slack_app]
}

output "slack_signing_secret_arn" {
  description = "Injected into the API and the worker as ACME_SLACK_SIGNING_SECRET; \"off\" until set, which refuses every call from Slack with 503."
  value       = aws_secretsmanager_secret.slack_app["slack_signing_secret"].arn
  depends_on  = [aws_secretsmanager_secret_version.slack_app]
}

output "operator_token_secret_names" {
  description = "The two token secrets by kind (provisioner, smoke); empty until the grant task mints one."
  value       = { for kind, secret in aws_secretsmanager_secret.operator_token : kind => secret.name }
}

output "operator_tokens_policy_arn" {
  description = "PutSecretValue on the two token secrets and nothing else; the grant task's role alone carries it."
  value       = aws_iam_policy.operator_tokens.arn
}

output "stripe_runtime_key_secret_arn" {
  description = "Injected into the API and the worker as ACME_STRIPE_RUNTIME_KEY; \"off\" until set, which leaves billing unconfigured."
  value       = aws_secretsmanager_secret.stripe["stripe_runtime_key"].arn
  depends_on  = [aws_secretsmanager_secret_version.stripe]
}

output "stripe_webhook_secret_arn" {
  description = "Injected into the API as ACME_STRIPE_WEBHOOK_SECRET; written by the Stripe bootstrap when it registers the endpoint."
  value       = aws_secretsmanager_secret.stripe["stripe_webhook_secret"].arn
  depends_on  = [aws_secretsmanager_secret_version.stripe]
}
