# The root's own inputs: the region and the environment name the provider
# tags with, and what the deploy workflow passes per run. Everything that
# makes this environment itself is in the module call in main.tf.

variable "region" {
  description = "AWS region, the one deployment/cloud/environments.json names."
  type        = string
  default     = "us-west-2"
}

variable "environment" {
  description = "Environment name, passed to every process as ACME_ENVIRONMENT and tagged on every resource."
  type        = string
  default     = "staging"
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
  description = "The API's public name, from deployment/cloud/environments.json; its hosted zone is the bootstrap root's."
  type        = string
}

variable "app_domain_name" {
  description = "The portal's public name, from deployment/cloud/environments.json; its hosted zone is the bootstrap root's."
  type        = string
}

variable "site_domain_name" {
  description = "The company site's public name, from deployment/cloud/environments.json: a record at Cloudflare, with its certificate the bootstrap root's. Empty, the default, leaves the site out; the deploy workflows pass it once SITE_DOMAIN_NAME is set and the certificate is issued."
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

variable "alarm_email" {
  description = "The address the environment's alarm topic delivers to; the deploy workflows pass the ALARM_EMAIL variable of the environment's GitHub environment."
  type        = string
}

variable "destroyable" {
  description = "Never set by a deploy. `scripts/cloud_nuke.sh` passes true on its apply before the destroy: buckets empty, the database skips its final snapshot and drops its deletion protection."
  type        = bool
  default     = false
}
