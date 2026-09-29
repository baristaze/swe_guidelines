variable "environment" {
  description = "Environment name."
  type        = string
}

variable "subnet_ids" {
  type = list(string)
}

variable "security_group_ids" {
  type = list(string)
}

variable "engine_version" {
  description = "Valkey <major>.<minor>; a 9.x version, to match default.valkey9."
  type        = string
  default     = "9.1"
}

variable "node_type" {
  type = string
}

variable "node_count" {
  description = "One primary plus replicas; more than one enables automatic failover."
  type        = number
}
