# Every input an environment has. A root declares no defaults for the scale
# inputs: its call is the parameter set, and reading the two calls side by
# side is how the environments are compared.

variable "region" {
  description = "AWS region."
  type        = string
}

variable "environment" {
  description = "Environment name, passed to every process as ACME_ENVIRONMENT. The set is the one acme.infra.impl.settings knows: the process refuses any other."
  type        = string

  validation {
    condition     = contains(["dev", "staging", "production"], var.environment)
    error_message = "environment must be one of dev, staging, production; a process refuses any other name."
  }
}

variable "api_image" {
  description = "The API image by digest (<registry>/acme-api@sha256:...); the deploy workflows pass it."
  type        = string
}

variable "maintenance_image" {
  description = "The maintenance worker image by digest; the deploy workflows pass it."
  type        = string
}

variable "api_domain_name" {
  description = "The API's public name, e.g. api.acme.example, or api.staging.acme.example for staging. A hosted zone of that name must exist in the account."
  type        = string
}

variable "app_domain_name" {
  description = "The portal's public name, e.g. app.acme.example, or app.staging.acme.example for staging. A hosted zone of that name must exist in the account."
  type        = string
}

variable "workos_client_id" {
  description = "The client id of the Acme App, the WorkOS application people of this environment sign in through: staging's in staging, production's in production. Not a secret; that application's own API key is (ACME_WORKOS_API_KEY, from the secrets module)."
  type        = string
}

variable "site_domain_name" {
  description = "The company site's public name: acme.example, or staging.acme.example for staging. A record in the Cloudflare zone, not a hosted zone here; an issued certificate for it must exist in us-east-1 (the bootstrap root's). Empty leaves the site out and changes nothing else."
  type        = string
  default     = ""
}

variable "cors_origins" {
  description = "Browser origins the API accepts besides the portal's, which is always allowed."
  type        = list(string)
  default     = []
}

variable "portal_sentry_dsn" {
  description = "The portal's error-reporting DSN (public by design), of the product's one tracker project and the same in every environment; the page tags its events with the environment. Empty turns browser reporting off."
  type        = string
  default     = ""
}

# Scale. These are what one environment differs from another by.

variable "vpc_cidr" {
  description = "The environment's own address space; no two environments share one."
  type        = string
}

variable "bucket_prefix" {
  description = "Bucket names are global; the prefix carries the environment."
  type        = string
}

variable "database_instance_class" {
  type = string
}

variable "database_multi_az" {
  type = bool
}

variable "database_deletion_protection" {
  type = bool
}

variable "database_pool_size" {
  description = "Connections each of a serving process's two pools may open (ACME_DATABASE_POOL_SIZE), the runtime login's and the system login's; the API's admission bounds follow it. A rollout may double the API's replicas, and the one-off tasks open a few more: the sum at the autoscaling ceilings stays under the instance class's max_connections, so the flip is safe (deployment/cloud/README.md)."
  type        = number
}

variable "cache_node_type" {
  type = string
}

variable "cache_node_count" {
  type = number
}

variable "api_desired_count" {
  type = number
}

variable "api_cpu" {
  type = number
}

variable "api_memory" {
  type = number
}

variable "maintenance_desired_count" {
  type = number
}

variable "maintenance_cpu" {
  type = number
}

variable "maintenance_memory" {
  type = number
}

# Operations. The alarm address, the one autoscaling switch with the per
# service levers under it, and the nuke's flag.

variable "alarm_email" {
  description = "The address the environment's alarm topic delivers to. Others subscribe to the topic by hand."
  type        = string
}

variable "autoscaling_enabled" {
  description = "The one flip. Every service's lever below it is on, so true scales the whole environment; false declares no scaling at all."
  type        = bool
}

variable "api_autoscaling" {
  description = "The API's lever: its ceiling, the CPU percent it tracks, and whether it takes part when the switch is on."
  type = object({
    enabled    = optional(bool, true)
    max        = number
    target_cpu = optional(number, 60)
  })
}

variable "maintenance_autoscaling" {
  description = "The maintenance worker's lever: its ceiling, the CPU percent it tracks, and whether it takes part when the switch is on."
  type = object({
    enabled    = optional(bool, true)
    max        = number
    target_cpu = optional(number, 60)
  })
}

variable "destroyable" {
  description = "True on the nuke's way down only: buckets empty on destroy, the database skips its final snapshot and drops its deletion protection, and the secrets skip their recovery window."
  type        = bool
}

variable "database_password_version" {
  description = "Raise it to rotate the database master password: a new one is generated and written, write-only, to the database and its URL secret, and every service rolls onto it. Between the database's change and the roll, running tasks fail their next new connection; rotate at a quiet hour."
  type        = number
  default     = 1
}
