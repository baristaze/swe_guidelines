variable "environment" {
  description = "Environment name."
  type        = string
}

variable "vpc_id" {
  type = string
}

variable "subnet_ids" {
  description = "Public subnets."
  type        = list(string)
}

variable "security_group_ids" {
  type = list(string)
}

variable "target_port" {
  description = "The port the API container listens on."
  type        = number
  default     = 8000
}

variable "health_check_path" {
  description = <<-EOT
    What the target group polls, which is liveness: /healthz answers
    whether the process is up, and reads no dependency. ECS replaces a task
    its target group calls unhealthy, so a readiness probe here would turn
    one database blip into every task replaced at once. /readyz stays the
    process's own answer on whether it can serve right now; the task
    definition's health check probes /healthz as well.
  EOT
  type        = string
  default     = "/healthz"
}

variable "certificate_arn" {
  description = "The validated ACM certificate for the API's domain name."
  type        = string
}
