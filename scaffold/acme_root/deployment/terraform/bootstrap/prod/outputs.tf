# What scripts/cloud_create.sh reads back: the GitHub variables of the
# `production-plan` and `production` environments, the profile it writes,
# and the delegation it makes at Cloudflare.

output "account_id" {
  value = module.account.account_id
}

output "plan_role_arn" {
  description = "The AWS_ROLE_ARN variable of the production-plan GitHub environment."
  value       = aws_iam_role.plan.arn
}

output "deploy_role_arn" {
  description = "The AWS_ROLE_ARN variable of the production GitHub environment."
  value       = module.deploy_role.arn
}

output "state_bucket" {
  description = "The TF_STATE_BUCKET variable of both production GitHub environments."
  value       = module.account.state_bucket
}

output "artifacts_bucket" {
  description = "The ARTIFACTS_BUCKET variable of this account's GitHub environments."
  value       = module.account.artifacts_bucket
}

output "investigate_role_arn" {
  description = "The role_arn of the acme-production-investigate profile."
  value       = module.account.investigate_role_arn
}

output "repository_urls" {
  value = module.account.repository_urls
}

output "name_servers" {
  description = "Each public name to its zone's name servers, written at Cloudflare as NS records."
  value       = module.account.name_servers
}

output "site_certificate_arn" {
  value = module.account.site_certificate_arn
}

output "site_certificate_validation" {
  description = "The site certificate's validation records, written at Cloudflare as CNAMEs by the create run."
  value       = module.account.site_certificate_validation
}
