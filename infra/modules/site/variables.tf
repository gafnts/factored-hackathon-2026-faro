variable "prefix" {
  description = "Prefix of every name in the environment, such as banking-agent-local"
  type        = string
}

variable "config" {
  description = "What the app reads from config.json at load (ADR-0007): public IDs and URLs only, never a customer's"
  type        = map(string)
}

variable "connect_src" {
  description = "Origins the page may call besides the site itself: Cognito's API and the Runtime"
  type        = list(string)
}

variable "domain_name" {
  description = "A custom hostname for the site, which requests its certificate; null serves the site on the distribution's own domain (ADR-0007)"
  type        = string
  default     = null
}

variable "attach_domain" {
  description = "Serve the site on domain_name, once the certificate's validation record is in DNS"
  type        = bool
  default     = false

  validation {
    condition     = !var.attach_domain || var.domain_name != null
    error_message = "attach_domain needs domain_name."
  }
}

variable "api_origin" {
  description = "The console API's own domain, served under /api on the site's origin, so the page calls it without CORS"
  type        = string
}

variable "log_retention_days" {
  type = number
}
