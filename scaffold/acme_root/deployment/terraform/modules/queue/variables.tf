variable "environment" {
  description = "Environment name."
  type        = string
}

variable "prefix" {
  description = "Queue name prefix; the process reads it as ACME_SQS_QUEUE_PREFIX."
  type        = string
}

variable "queues" {
  description = "One entry per member of acme.infra.queues.Queues."
  type        = list(string)
}

variable "max_receives" {
  description = "Attempts before a message moves to its dead-letter queue."
  type        = number
  default     = 5
}

variable "visibility_timeout_seconds" {
  type    = number
  default = 60
}

variable "retention_seconds" {
  description = "How long an unconsumed message waits; four days."
  type        = number
  default     = 345600
}
