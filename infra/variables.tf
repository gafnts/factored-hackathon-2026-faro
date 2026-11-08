variable "project_name" {
  type    = string
  default = "banking-agent"
}

variable "environment" {
  description = "Tagged on every resource; each deploy role can only touch its own environment's."
  type        = string
  validation {
    condition     = contains(["local", "prototype"], var.environment)
    error_message = "environment must be one of: local, prototype."
  }
}

variable "aws_region" {
  type    = string
  default = "us-east-1"
}

variable "tools_data_export" {
  description = "The gold export the tools' data is created from; only a reviewed change sets it (ADR-0006, decision 1)"
  type = object({
    snapshot         = string
    pipeline_version = string
  })
  validation {
    condition     = can(regex("^[0-9a-f]{16}$", var.tools_data_export.snapshot)) && can(regex("^[0-9a-f]{16}$", var.tools_data_export.pipeline_version))
    error_message = "snapshot and pipeline_version are 16 hexadecimal digits each."
  }
}

variable "domain_name" {
  description = "A custom hostname for the site; null serves it on its CloudFront domain, as a fork does (ADR-0007)"
  type        = string
  default     = null
}

variable "attach_domain" {
  description = "Serve the site on domain_name; set it once the certificate's validation record is in DNS (ADR-0007)"
  type        = bool
  default     = false
}

variable "log_retention_days" {
  description = "Retention of every log group the stack creates (ADR-0004, Data retention)"
  type        = number
  default     = 30
}

variable "turns_per_minute" {
  description = "A sign-in's turns a minute before the entrypoint refuses one (ADR-0004, decision 21)"
  type        = number
  default     = 10
}

variable "turns_per_day" {
  description = "A user's turns a UTC day before the entrypoint refuses one; well above a day of grading (decision 21)"
  type        = number
  default     = 500
}

# 5 USD a day, a tenth of the model's monthly limit, at the freeze's 95th percentile of 0.645 cents a turn.
variable "turns_cap" {
  description = "Invited users' and visitors' turns a UTC day before the entrypoint refuses one; written once, then set with make cap, 0 pausing (ADR-0004, decision 21)"
  type        = number
  default     = 750
}
