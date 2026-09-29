output "api_url" {
  description = "Where the API answers."
  value       = "https://${var.api_domain_name}"
}

output "portal_url" {
  description = "Where the portal answers."
  value       = module.portal.url
}

# What the deploy workflows need to publish the portal build.

output "portal_bucket" {
  value = module.portal.bucket_name
}

output "portal_distribution_id" {
  value = module.portal.distribution_id
}

# And the company site's, which the same workflows publish the same way.
# Empty strings when the environment has no site.

output "site_url" {
  description = "Where the company site answers."
  value       = try(module.site[0].url, "")
}

output "site_bucket" {
  value = try(module.site[0].bucket_name, "")
}

output "site_distribution_id" {
  value = try(module.site[0].distribution_id, "")
}

output "site_distribution_domain_name" {
  description = "What the site's name at Cloudflare is a CNAME to; the create run writes it."
  value       = try(module.site[0].distribution_domain_name, "")
}

# What `aws ecs run-task` needs to start a one-off task on the image the
# apply just rolled out: the cluster, the task definition and its container
# (the command override names it), and the network (the private subnets,
# the app security group, no public address).

output "cluster_name" {
  value = module.cluster.name
}

output "api_task_definition_arn" {
  value = module.api.task_definition_arn
}

output "migrate_task_definition_arn" {
  description = "The migrate task: `acme-api migrate ensure-logins`, then `acme-api migrate --all`."
  value       = module.migrate.task_definition_arn
}

output "migrate_container_name" {
  value = module.migrate.container_name
}

output "grant_task_definition_arn" {
  description = "The grant task: `acme-api grant-operator ...` as the command override."
  value       = module.grant.task_definition_arn
}

output "grant_container_name" {
  value = module.grant.container_name
}

output "operator_token_secret_names" {
  description = "The secrets the grant task writes a minted token to, by kind (provisioner, smoke)."
  value       = module.secrets.operator_token_secret_names
}

output "private_subnet_ids" {
  value = module.network.private_subnet_ids
}

output "app_security_group_id" {
  value = module.network.app_security_group_id
}

# What an operator opens and subscribes to.

output "dashboard_name" {
  description = "The CloudWatch dashboard's name."
  value       = module.dashboard.name
}

output "alarm_topic_arn" {
  description = "The topic every alarm goes to; another address subscribes to it by hand."
  value       = module.alarms.topic_arn
}

output "alarm_names" {
  value = module.alarms.alarm_names
}

# The files whose fingerprint decides whether the migrate task runs. A plan
# that found none, or missed the migrations directory, would key the run on
# a constant and never migrate again, so it stops here instead.
output "migration_files" {
  description = "The repository's files deployment/migration-inputs.json names, as the plan found them."
  value       = local.migration_files

  precondition {
    condition     = contains(local.migration_files, "om/migrations/env.py") && contains(local.migration_files, "om/src/acme/om/storage/migrate.py")
    error_message = "the migration inputs were not found from the environment module; the repository root is four levels above it"
  }
}
