variable "prefix" {
  description = "Prefix of every name in the environment, such as banking-agent-local; also the metrics' namespace"
  type        = string
}

variable "guest_log_group" {
  description = "The guest entry's log group, whose line per click two alarms read"
  type        = string
}

variable "log_group_name" {
  description = "The Runtime's log group, whose JSON lines the metric filters count"
  type        = string
}

variable "service_name" {
  description = "The Runtime's service name, which its spans carry and the saved queries filter on"
  type        = string
}

variable "fallback_share" {
  description = "The share of the model's replies falling back to fixed text, over an hour, that sets off its alarm"
  type        = number
  default     = 0.2
}

variable "fewest_checks" {
  description = "The checked replies an hour must hold before its fallback share is read"
  type        = number
  default     = 10
}
