variable "environment" {
  description = "The environment this role reads, and the only one it may see."
  type        = string

  validation {
    condition     = contains(["staging", "production"], var.environment)
    error_message = "environment must be staging or production."
  }
}

variable "other_environment" {
  description = "The environment this role must never reach. Resources tagged with it are denied outright."
  type        = string
}

variable "state_bucket" {
  description = "This account's state bucket."
  type        = string
}

variable "state_key_prefix" {
  description = "This environment's prefix inside the state bucket, without a trailing slash, e.g. environments/staging."
  type        = string
}

variable "operator_principal_arn_patterns" {
  description = "ARN patterns of the principals in this account that may assume this role, e.g. the Identity Center PowerUserAccess role."
  type        = list(string)
}
