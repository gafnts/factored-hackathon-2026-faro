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

variable "issuer" {
  description = "The user pool's issuer URL, which the authorizer checks tokens against"
  type        = string
}

variable "staff_client_id" {
  description = "The staff app client, the only one whose access tokens the console API accepts"
  type        = string
}

variable "cases_table" {
  type = object({
    name = string
    arn  = string
  })
}

variable "execution_records_table" {
  description = "The execution records, whose turns a case names as its evidence"
  type = object({
    name = string
    arn  = string
  })
}

variable "route_limits" {
  description = "Routes another module adds to the API, each throttled on its own below the stage's limit"
  type = map(object({
    rate  = number
    burst = number
  }))
  default = {}
}

variable "rate_limit" {
  description = "Requests a second the stage allows across callers; an open console makes about 0.7 (ADR-0007, Freshness)"
  type = object({
    rate  = number
    burst = number
  })
  default = { rate = 20, burst = 40 }
}
