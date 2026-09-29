variable "environment" {
  description = "The environment this role applies, and the only one it may touch."
  type        = string

  validation {
    condition     = contains(["staging", "production"], var.environment)
    error_message = "environment must be staging or production."
  }
}

variable "other_environment" {
  description = "The other environment. It lives in another account; resources tagged with it are denied as well, a backstop for a root applied in the wrong account."
  type        = string
}

variable "github_repository" {
  description = "owner/name of the repository whose jobs may assume this role."
  type        = string
}

variable "github_repository_id" {
  description = "The repository's immutable id; the trust matches it beside the name in the subject."
  type        = string
}

variable "github_repository_owner_id" {
  description = "The repository owner's immutable id."
  type        = string
}

variable "github_environment" {
  description = "The GitHub environment a job must declare to assume this role. The subject condition names it, so a job without it presents its branch instead and is refused."
  type        = string
}

variable "github_ref" {
  description = "The only ref a job assuming this role may run on, e.g. refs/heads/main or refs/heads/release."
  type        = string
}

variable "oidc_provider_arn" {
  description = "The GitHub OIDC provider in this account."
  type        = string
}

variable "state_bucket" {
  description = "This account's state bucket."
  type        = string
}

variable "artifacts_bucket" {
  description = "This account's artifacts bucket: the static builds kept by commit."
  type        = string
}

variable "state_key_prefix" {
  description = "This environment's prefix inside the state bucket, without a trailing slash, e.g. environments/staging."
  type        = string
}

variable "image_repositories" {
  description = "The registry repositories this role may read."
  type        = list(string)
}

variable "promote_images" {
  description = "True for production, which tags each digest it promotes `prod-<sha>` so the registry's lifecycle keeps it."
  type        = bool
}

variable "dns_record_patterns" {
  description = "The record names this role may change, as IAM name patterns: its own two names and the validation records under them."
  type        = list(string)
}

variable "task_boundary_policy_arn" {
  description = "The permissions boundary every role this role creates must carry. A role created without it is refused, so the deploy role cannot mint a task role wider than itself."
  type        = string
}
