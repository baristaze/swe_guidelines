variable "anomaly_monitor" {
  description = "Declare the cost anomaly monitor. scripts/cloud_create.sh sets it false while Cost Explorer is not on for the account."
  type        = bool
  default     = true
}

variable "owner_email" {
  description = "Where the budget's notifications and the anomaly monitor's reports go."
  type        = string
}

variable "monthly_budget_usd" {
  description = "The monthly cost budget for staging's account, in USD. Sized for XS, about 115 a month; deployment/cloud/README.md prices every size."
  type        = number
  default     = 200
}

variable "replicate_to_production" {
  description = "Copy every image and static build into production's account. scripts/cloud_create.sh sets it true once production's artifacts bucket exists; S3 refuses a replication rule whose destination does not."
  type        = bool
  default     = false
}
