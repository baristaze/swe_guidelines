# Forwarded from the environment module, which is where they are described.

output "api_url" {
  description = "Where the API answers."
  value       = module.environment.api_url
}

output "portal_url" {
  description = "Where the portal answers."
  value       = module.environment.portal_url
}

# What the deploy workflows need to publish the portal build.

output "portal_bucket" {
  value = module.environment.portal_bucket
}

output "portal_distribution_id" {
  value = module.environment.portal_distribution_id
}

# And the company site's.

output "site_url" {
  description = "Where the company site answers."
  value       = module.environment.site_url
}

output "site_bucket" {
  value = module.environment.site_bucket
}

output "site_distribution_id" {
  value = module.environment.site_distribution_id
}

output "site_distribution_domain_name" {
  value = module.environment.site_distribution_domain_name
}

# What `aws ecs run-task` needs to start a one-off task (the migration, an
# operator grant) on the image the apply just rolled out.

output "cluster_name" {
  value = module.environment.cluster_name
}

output "api_task_definition_arn" {
  value = module.environment.api_task_definition_arn
}

output "migrate_task_definition_arn" {
  value = module.environment.migrate_task_definition_arn
}

output "migrate_container_name" {
  value = module.environment.migrate_container_name
}

output "grant_task_definition_arn" {
  value = module.environment.grant_task_definition_arn
}

output "grant_container_name" {
  value = module.environment.grant_container_name
}

output "operator_token_secret_names" {
  value = module.environment.operator_token_secret_names
}

output "private_subnet_ids" {
  value = module.environment.private_subnet_ids
}

output "app_security_group_id" {
  value = module.environment.app_security_group_id
}

# What an operator opens and subscribes to.

output "dashboard_name" {
  value = module.environment.dashboard_name
}

output "alarm_topic_arn" {
  value = module.environment.alarm_topic_arn
}
