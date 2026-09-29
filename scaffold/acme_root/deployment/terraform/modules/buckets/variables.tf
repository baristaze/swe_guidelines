variable "environment" {
  description = "Environment name."
  type        = string
}

variable "prefix" {
  description = "Bucket name prefix; the process reads it as ACME_S3_BUCKET_PREFIX. Bucket names are global, so it carries the environment."
  type        = string
}

variable "buckets" {
  description = "One entry per member of acme.infra.buckets.Buckets."
  type        = list(string)
}

variable "browser_buckets" {
  description = "The buckets a browser posts to and reads from with a presigned URL, and so the ones that answer its preflight. Each is one of var.buckets."
  type        = list(string)
  default     = []

  validation {
    condition     = alltrue([for name in var.browser_buckets : contains(var.buckets, name)])
    error_message = "every browser bucket is one of the buckets."
  }
}

variable "browser_origins" {
  description = "The origins a browser bucket answers: the portal's, never a wildcard."
  type        = list(string)
  default     = []

  validation {
    condition     = !contains(var.browser_origins, "*")
    error_message = "a browser bucket names its origins; a wildcard would let any page post a form it was handed."
  }
}

variable "object_key_patterns" {
  description = "Per bucket, the object keys the task role may read, write, and delete, as an S3 resource pattern under the bucket; a bucket not named gets every key. A key is <org_id>/<the caller's key>, so a prefix the code chooses sits after the first segment."
  type        = map(string)
  default     = {}
}

variable "noncurrent_version_days" {
  description = "How long a deleted or overwritten object's previous version is kept before it expires. The buckets are versioned, so a delete alone frees nothing."
  type        = number
  default     = 7
}

variable "destroyable" {
  description = "True on the nuke's way down only: a destroy then empties the buckets."
  type        = bool
}
