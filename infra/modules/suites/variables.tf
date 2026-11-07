variable "prefix" {
  description = "Prefix of every name in the environment, such as banking-agent-local"
  type        = string
}

variable "permissions_boundary_arn" {
  type = string
}

variable "user_pool_id" {
  description = "The pool the suites create their test users in, sign them in, and delete them from"
  type        = string
}

variable "overlay_table" {
  description = "The sandbox's overlay, where the suites write fixtures and fault plans"
  type = object({
    name = string
    arn  = string
  })
}

variable "confirmations_table" {
  type = object({
    name = string
    arn  = string
  })
}

variable "cases_table" {
  description = "The handoff cases"
  type = object({
    name = string
    arn  = string
  })
}

variable "runtime_tables" {
  description = "The Runtime's own tables, by name"
  type = object({
    checkpoints       = string
    execution_records = string
    usage_counters    = string
  })
}

variable "tools_data_table" {
  description = "The tools' data table, which holds every customer's rows"
  type = object({
    name = string
    arn  = string
  })
}

variable "file_handoff_function" {
  description = "file_handoff, which the access tests call directly with another customer's payload or token"
  type = object({
    name = string
    arn  = string
  })
}

variable "runtime_log_group" {
  type = string
}

variable "console_api_id" {
  type = string
}
