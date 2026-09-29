variable "environment" {
  description = "The environment this account holds, and the only one."
  type        = string

  validation {
    condition     = contains(["staging", "production"], var.environment)
    error_message = "environment must be staging or production."
  }
}

variable "other_environment" {
  description = "The environment this account does not hold. Anything tagged with it is denied to the investigate role, in case a root is ever applied in the wrong account."
  type        = string
}

variable "state_key_prefix" {
  description = "The environment root's prefix inside the state bucket, without a trailing slash, e.g. environments/staging."
  type        = string
}

variable "images" {
  description = "One registry per image under deployment/docker/."
  type        = list(string)
  default     = ["acme-api", "acme-maintenance"]
}

variable "api_domain_name" {
  description = "The API's public name. It gets a hosted zone of its own, delegated from the domain's zone at Cloudflare."
  type        = string
}

variable "app_domain_name" {
  description = "The portal's public name. It gets a hosted zone of its own, delegated from the domain's zone at Cloudflare."
  type        = string
}

variable "site_domain_name" {
  description = "The company site's public name: a record in the domain's zone at Cloudflare, not a zone of its own. Its certificate, in us-east-1, is made here."
  type        = string
}

variable "sign_in_role_name" {
  description = "The Identity Center permission set a person signs in to this account with, and the investigate role trusts: PowerUserAccess in staging, a read-only set in production."
  type        = string
}

variable "operator_principal_arn_patterns" {
  description = "ARN patterns of the principals that may assume the investigate role. Empty means this account's Identity Center role for sign_in_role_name."
  type        = list(string)
  default     = []
}

variable "owner_email" {
  description = "Where the budget's notifications and the anomaly monitor's reports go."
  type        = string
}

variable "monthly_budget_usd" {
  description = "The monthly cost budget for the account, in USD. deployment/cloud/README.md prices every size."
  type        = number
}

variable "anomaly_monitor" {
  description = "Declare the cost anomaly monitor and its subscription. False while the organization has not turned Cost Explorer on for the account, which the monitor needs."
  type        = bool
  default     = true
}

variable "anomaly_impact_usd" {
  description = "The absolute impact, in USD, below which a cost anomaly is not reported."
  type        = number
  default     = 20
}

variable "audit_retention_days" {
  description = "How long the account's CloudTrail logs are kept."
  type        = number
  default     = 365
}
