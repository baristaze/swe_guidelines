variable "environment" {
  description = "Environment name."
  type        = string
}

variable "cidr" {
  description = "The VPC's address block; each environment has its own so they can peer later."
  type        = string
}

variable "availability_zone_count" {
  description = "How many zones get a public and a private subnet."
  type        = number
  default     = 2
}

variable "app_port" {
  description = "The port the load balancer reaches application tasks on."
  type        = number
  default     = 8000
}
