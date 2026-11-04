variable "project_name" {
  description = "Prefix for the deploy roles, the service roles they are allowed to manage, the model key secrets, and the budget"
  type        = string
  default     = "banking-agent"
}

variable "aws_region" {
  type    = string
  default = "us-east-1"
}

# No default, so a fork can't end up trusting this repository. Written by make bootstrap.
variable "github_oidc_subject_prefix" {
  description = "Prefix of the sub claim in the CI repository's OIDC tokens (gh api repos/<owner>/<repo>/actions/oidc/customization/sub)"
  type        = string

  validation {
    condition     = startswith(var.github_oidc_subject_prefix, "repo:")
    error_message = "github_oidc_subject_prefix must start with \"repo:\"; re-run make bootstrap."
  }
}

variable "local_principal_arn" {
  description = "IAM user/role ARN allowed to assume the local-deploy role"
  type        = string
}

variable "state_bucket_name" {
  description = "Name of the S3 bucket holding Terraform state"
  type        = string
}

variable "monthly_budget_usd" {
  description = "The account's monthly cost budget in USD, or null for none"
  type        = number
  default     = 40
}

variable "spans_retention_days" {
  description = "How long aws/spans keeps every environment's spans"
  type        = number
  default     = 30
}

variable "spans_indexed_percentage" {
  description = "The share of spans Transaction Search indexes for search and the GenAI page; all of them until strangers can sign up"
  type        = number
  default     = 100
}
