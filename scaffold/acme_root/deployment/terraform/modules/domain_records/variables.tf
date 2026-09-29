variable "api_zone_id" {
  description = "The zone whose apex is api_domain_name."
  type        = string
}

variable "app_zone_id" {
  description = "The zone whose apex is app_domain_name."
  type        = string
}

variable "api_domain_name" {
  type = string
}

variable "app_domain_name" {
  type = string
}

variable "load_balancer_dns_name" {
  type = string
}

variable "load_balancer_zone_id" {
  type = string
}

variable "distribution_domain_name" {
  type = string
}

variable "distribution_zone_id" {
  type = string
}
