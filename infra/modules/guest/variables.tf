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

variable "tools_zip" {
  description = "The tools' zip, which every Lambda of ours but the pre-token trigger runs, written by make build"
  type        = string
}

variable "user_pool" {
  description = "The pool the guest entry makes visitors in, and the cleanup deletes them from"
  type = object({
    id  = string
    arn = string
  })
}

variable "customer_client_id" {
  description = "The customers' app client, which the guest entry signs each visitor in through"
  type        = string
}

variable "usage_counters_table" {
  description = "The Runtime's usage counters, which hold the cap and the day's guests (ADR-0004, decision 21)"
  type = object({
    name = string
    arn  = string
  })
}

variable "gateway_url" {
  description = "The Gateway, whose list_cards the guest entry calls with the visitor's token for the welcome"
  type        = string
}

variable "list_cards_target" {
  description = "The Gateway target that serves list_cards"
  type        = string
}

variable "api" {
  description = "The console API, which serves the guest entry's route beside its own"
  type = object({
    id            = string
    execution_arn = string
  })
}

variable "concurrency" {
  description = "The guest entry's reserved concurrency, which bounds how fast it makes users (ADR-0007, The guest entry)"
  type        = number
  default     = 2
}
