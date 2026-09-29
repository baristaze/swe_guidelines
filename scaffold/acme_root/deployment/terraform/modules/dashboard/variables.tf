variable "environment" {
  description = "Environment name; the dashboard's name and the dimension that selects this environment's application metrics."
  type        = string
}

variable "cluster_name" {
  description = "The container cluster the services run on."
  type        = string
}

variable "service_names" {
  description = "The services whose running task counts the dashboard shows."
  type        = list(string)
}

variable "database_identifier" {
  description = "The database instance identifier, e.g. acme-staging."
  type        = string
}

variable "load_balancer_arn_suffix" {
  description = "The load balancer's ARN suffix, the dimension its CloudWatch metrics carry; the latency widget reads it."
  type        = string
}

variable "cache_node_ids" {
  description = "The cache cluster ids the replication group is made of, e.g. acme-staging-001."
  type        = list(string)
}

variable "queue_names" {
  description = "The queue names, without the -dead suffix; the dashboard shows both."
  type        = list(string)
}

variable "http_statuses" {
  description = "The status codes the responses-by-status widget sums, one series each; CloudWatch has no group-by, so the list is explicit."
  type        = list(number)
  default     = [200, 201, 204, 400, 401, 403, 404, 409, 422, 429, 500, 503]
}

variable "read_latency_routes" {
  description = "The GET routes the alarms module keeps a latency alarm on; one line each."
  type        = list(string)
  default     = []
}
