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
  description = "The monthly cost budget for production's account, in USD. Sized for S, about 130 a month; deployment/cloud/README.md prices every size."
  type        = number
  default     = 200
}
