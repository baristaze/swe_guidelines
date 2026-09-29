variable "environment" {
  description = "Environment name."
  type        = string
}

variable "prefix" {
  description = "Name prefix of every secret this environment owns, e.g. acme/staging/."
  type        = string
}

variable "database_password" {
  description = "The master password, generated for the run and never stored; the master URL is written write-only."
  type        = string
  sensitive   = true
  ephemeral   = true
}

variable "database_password_version" {
  description = "The database module's password version; a bump writes the four URLs again, each login's with a new password."
  type        = number
}

variable "database_username" {
  description = "The master user's name."
  type        = string
}

variable "database_address" {
  type = string
}

variable "database_port" {
  type = number
}

variable "database_name" {
  type = string
}

variable "destroyable" {
  description = "True on the nuke's way down only: the secrets are deleted at once, so a rebuild within the recovery window can create them again under the same names."
  type        = bool
}

variable "edge_secret_version" {
  description = "The edge secret's version; a raise writes a new value to the secret and to the portal's distribution in one apply."
  type        = number
  default     = 1
}
