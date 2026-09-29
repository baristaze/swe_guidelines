variable "name" {
  description = "Service name, as it appears in the service catalog; also the container name."
  type        = string
}

variable "image" {
  description = "Image reference by digest; production promotes what staging already ran."
  type        = string
}

variable "environment" {
  description = "Environment name, passed to the process as ACME_ENVIRONMENT."
  type        = string
}

variable "cluster_arn" {
  type = string
}

variable "subnet_ids" {
  description = "Private subnets tasks run in."
  type        = list(string)
}

variable "security_group_ids" {
  type = list(string)
}

variable "desired_count" {
  description = "Number of replicas."
  type        = number
  default     = 1
}

variable "cpu" {
  description = "CPU units per task."
  type        = number
  default     = 256
}

variable "memory" {
  description = "Memory in MiB per task."
  type        = number
  default     = 512
}

variable "port" {
  description = "Container port; null for a worker with no network surface."
  type        = number
  default     = null
}

variable "command" {
  description = "Overrides the image's CMD; null keeps it."
  type        = list(string)
  default     = null
}

variable "environment_variables" {
  description = "Plain environment for the process, every key under the ACME_ prefix."
  type        = map(string)
  default     = {}
}

variable "secrets" {
  description = "Environment injected from Secrets Manager: variable name to secret ARN."
  type        = map(string)
  default     = {}
}

variable "rollback_secret_arns" {
  description = "Secrets the previous revision injects and this one does not. The execution role keeps reading them, so a rolled-back rollout starts; the process never sees them."
  type        = list(string)
  default     = []
}

variable "policy_arns" {
  description = "Policies the task role gets: the queues, buckets, and secrets the process uses."
  type        = list(string)
  default     = []
}

variable "target_group_arn" {
  description = "Registers the container with a load balancer; null for a worker."
  type        = string
  default     = null
}

variable "health_check_command" {
  description = "The container health check, in the ECS command form; null disables it."
  type        = list(string)
  default     = null
}

variable "deployment_maximum_percent" {
  description = "Replicas during a rollout, as a percentage of desired. A worker holds leases, so a worker instance passes 100: never more than desired."
  type        = number
  default     = 200
}

variable "deployment_minimum_healthy_percent" {
  description = "Replicas that must stay running during a rollout, as a percentage of desired."
  type        = number
  default     = 100
}

variable "stop_timeout_seconds" {
  description = "Time between SIGTERM and SIGKILL; a worker needs enough to return its leases."
  type        = number
  default     = 30
}

variable "log_retention_days" {
  type    = number
  default = 30
}

variable "metrics_port" {
  description = "Port the process serves /metrics on, on localhost; the collector sidecar scrapes it."
  type        = number
}

variable "collector_image" {
  description = "The AWS Distro for OpenTelemetry collector image the sidecar runs."
  type        = string
  default     = "public.ecr.aws/aws-observability/aws-otel-collector:v0.50.0"
}

variable "pre_rollout" {
  description = "Commands run in order, in one one-off task on the named task definition and container, before the service rolls: the migration. They run when `triggers` or the commands change; with no triggers, whenever this service's task definition or that one changes. Null runs nothing."
  type = object({
    task_definition_arn = string
    container           = string
    commands            = list(list(string))
    triggers            = optional(map(string))
  })
  default = null
}

variable "rollout_after" {
  description = "A value the rollout waits for: another instance's rollout_gate output, so a worker rolls only after the API's migration ran."
  type        = string
  default     = ""
}

variable "autoscaling" {
  description = "Target-tracking autoscaling on CPU. On, it declares a scaling target whose floor is desired_count and whose ceiling is max, and a policy that holds the service's average CPU at target_cpu percent; off, it declares nothing. The environment module passes the root's one switch through here."
  type = object({
    enabled    = bool
    max        = number
    target_cpu = number
  })

  validation {
    condition     = var.autoscaling.max >= var.desired_count
    error_message = "autoscaling.max must be at least desired_count: the floor is the desired count, and a ceiling below it is not a range."
  }
}
