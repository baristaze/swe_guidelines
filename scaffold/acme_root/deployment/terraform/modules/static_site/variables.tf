variable "name" {
  description = "What the site is, in its resources' names and descriptions: portal or site."
  type        = string

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]*$", var.name))
    error_message = "name is lowercase letters, digits, and dashes."
  }
}

variable "environment" {
  description = "Environment name, in resource names and tags."
  type        = string
}

variable "bucket_name" {
  description = "The private bucket the built site is uploaded to."
  type        = string
}

variable "domain_name" {
  description = "The site's domain name, e.g. app.acme.example or acme.example."
  type        = string
}

variable "certificate_arn" {
  description = "A validated ACM certificate in us-east-1 for domain_name."
  type        = string
}

variable "api_domain_name" {
  description = "The API's own domain name, e.g. api.acme.example. Given, the API's paths (api_path_patterns) are served from this distribution by its load balancer, so the page calls it same-origin. Empty for a page that calls no API."
  type        = string
  default     = ""

  validation {
    condition     = var.api_domain_name == "" || can(regex("^[a-z0-9.-]+$", var.api_domain_name))
    error_message = "api_domain_name is a bare host name, with no scheme and no path."
  }

  # A custom error page answers every behavior's errors, the API's too: its
  # 404 would reach the page as HTML.
  validation {
    condition     = var.api_domain_name == "" || var.not_found_page == ""
    error_message = "a site that serves the API has no not_found_page, which would replace the API's own errors."
  }

  # Without the edge secret the API cannot tell this distribution from any
  # other, and counts every request by the edge's address.
  validation {
    condition     = var.api_domain_name == "" || length(var.api_edge_secret) >= 32
    error_message = "a site that serves the API sends an api_edge_secret of 32 characters or more."
  }
}

variable "api_path_patterns" {
  description = "The paths that go to the API, the realtime socket's included; every one the page calls. None may be a path the site serves itself."
  type        = list(string)
  default     = ["/v1/*"]

  validation {
    condition     = alltrue([for pattern in var.api_path_patterns : startswith(pattern, "/") && !contains(["/", "/*", "/assets/*", "/config.json", "/index.html"], pattern)])
    error_message = "an API path is a path from the root, and never one of the site's own files."
  }
}

variable "api_edge_secret" {
  description = "Sent to the API as the X-Acme-Edge header on every request this distribution forwards; the API trusts the viewer address CloudFront appended to X-Forwarded-For only beside it."
  type        = string
  default     = ""
  sensitive   = true
}

variable "store_origins" {
  description = "The object store's origins the page posts a file to and fetches one from, by a URL the API signed. They join connect-src, and img-src, media-src, and frame-src for a preview. Empty for a page that holds no files."
  type        = list(string)
  default     = []

  validation {
    condition     = alltrue([for origin in var.store_origins : can(regex("^https://[^/*]+$", origin))])
    error_message = "a store origin is https://host, with no path and no wildcard."
  }
}

variable "sentry_dsn" {
  description = "The page's error-reporting DSN; browser DSNs are public by design. Empty turns reporting off. Its origin joins the Content-Security-Policy's connect-src."
  type        = string
  default     = ""

  validation {
    condition     = var.sentry_dsn == "" || can(regex("^(https?://)[^@/]+@([^/]+)", var.sentry_dsn))
    error_message = "sentry_dsn is empty or scheme://key@host/project."
  }
}

variable "runtime_config" {
  description = "Written as /config.json, which the page reads before it renders. Empty writes no file."
  type        = map(string)
  default     = {}
}

variable "client_routes" {
  description = "True for a single-page app: a path without a file extension gets index.html."
  type        = bool
  default     = false
}

variable "not_found_page" {
  description = "The page a missing path gets, with a 404, e.g. /404.html. Empty leaves CloudFront's own answer."
  type        = string
  default     = ""

  validation {
    condition     = var.not_found_page == "" || startswith(var.not_found_page, "/")
    error_message = "not_found_page is empty or a path from the root, e.g. /404.html."
  }
}

variable "price_class" {
  description = "Which edge locations serve the site."
  type        = string
  default     = "PriceClass_100"
}

variable "destroyable" {
  description = "True on the nuke's way down only: a destroy then empties the bucket."
  type        = bool
  default     = false
}
