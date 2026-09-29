variable "name" {
  description = "The task's name, e.g. migrate or grant; also its container's name."
  type        = string
}

variable "image" {
  description = "Image reference by digest."
  type        = string
}

variable "environment" {
  description = "Environment name."
  type        = string
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

variable "command" {
  description = "Overrides the image's CMD; null keeps it. A run passes its own command as an override."
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

variable "policy_arns" {
  description = "Policies the task role gets."
  type        = list(string)
  default     = []
}

variable "log_retention_days" {
  type    = number
  default = 30
}
