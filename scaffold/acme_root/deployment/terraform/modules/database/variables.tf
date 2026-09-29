variable "environment" {
  description = "Environment name."
  type        = string
}

variable "subnet_ids" {
  description = "Private subnets the instance may live in."
  type        = list(string)
}

variable "security_group_ids" {
  description = "Security groups on the instance; ingress comes from the app group."
  type        = list(string)
}

variable "engine_version" {
  description = "Postgres major version; ADR 0002 names 18."
  type        = string
  default     = "18"
}

variable "instance_class" {
  type = string
}

variable "allocated_storage" {
  description = "Storage in GiB; autoscales up to five times this."
  type        = number
  default     = 20
}

variable "multi_az" {
  type = bool
}

variable "deletion_protection" {
  description = "Refuse a delete of the instance. Production's is true; the nuke lifts it through destroyable."
  type        = bool
}

variable "destroyable" {
  description = "True on the nuke's way down only: lifts the deletion protection and skips the final snapshot."
  type        = bool
}

variable "backup_retention_days" {
  type    = number
  default = 7
}

variable "master_password" {
  description = "The master user's password, generated for the run and never stored. Written write-only."
  type        = string
  sensitive   = true
  ephemeral   = true
}

variable "master_password_version" {
  description = "Bump to write a new master password; the secret module takes the same number."
  type        = number
}
