variable "environment" {
  description = "Environment name."
  type        = string
}

variable "domain_name" {
  description = "The one name the certificate covers."
  type        = string
}

variable "zone_id" {
  description = "The Route 53 zone the validation records go in."
  type        = string
}
