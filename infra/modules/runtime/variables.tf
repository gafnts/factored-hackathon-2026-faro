variable "prefix" {
  description = "Prefix of every name in the environment, such as banking-agent-local"
  type        = string
}

variable "permissions_boundary_arn" {
  type = string
}

variable "log_retention_days" {
  type = number
}

variable "discovery_url" {
  description = "The user pool's OpenID configuration"
  type        = string
}

variable "allowed_clients" {
  description = "App clients whose access tokens the Runtime accepts"
  type        = list(string)
}

variable "customer_client_id" {
  description = "The customers' app client, which the entrypoint checks tokens against"
  type        = string
}

variable "required_group" {
  description = "Cognito group a token's cognito:groups claim must contain"
  type        = string
}

variable "gateway_url" {
  type = string
}

variable "gateway_targets" {
  description = "Each tool's target, which names the tool as <target>___<tool>"
  type        = map(string)
}

variable "confirmations_table" {
  description = "The confirmations the Runtime creates and the control answers"
  type = object({
    name = string
    arn  = string
  })
}

variable "file_handoff_function" {
  description = "The Lambda the Runtime invokes to file a handoff, off the Gateway"
  type = object({
    name = string
    arn  = string
  })
}

variable "tools_data" {
  description = "The stamp and the clock of the export the tools read, which every turn's record carries"
  type = object({
    stamp = object({ snapshot = string, pipeline_version = string })
    clock = object({ business_date = string, as_of = string })
  })
}

variable "runtime_zip" {
  description = "The Runtime's zip, written by make build"
  type        = string
}

variable "turns_per_minute" {
  description = "A sign-in's turns a minute before the entrypoint refuses one (decision 21)"
  type        = number
}

variable "turns_per_day" {
  description = "A user's turns a UTC day before the entrypoint refuses one (decision 21)"
  type        = number
}

variable "turns_cap" {
  description = "Invited users' and visitors' turns a UTC day before the entrypoint refuses one, written once (decision 21)"
  type        = number
}
