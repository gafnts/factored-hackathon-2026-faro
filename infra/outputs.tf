output "aws_account_id" {
  description = "Account the stack is deployed into. Doubles as a canary that the deploy role and backend are wired correctly."
  value       = data.aws_caller_identity.current.account_id
}

output "environment" {
  value = var.environment
}

output "prefix" {
  description = "Prefix of every name in the environment"
  value       = local.prefix
}

output "permissions_boundary_arn" {
  description = "Boundary every IAM role in this stack must carry"
  value       = local.permissions_boundary_arn
}

output "user_pool_id" {
  value = module.identity.user_pool_id
}

output "customer_client_id" {
  value = module.identity.customer_client_id
}

output "staff_client_id" {
  value = module.identity.staff_client_id
}

output "gateway_url" {
  value = module.gateway.gateway_url
}

output "gateway_targets" {
  description = "Each tool's target; a tool's action is named <target>___<tool>"
  value       = module.gateway.targets
}

output "sandbox_tables" {
  description = "The sandbox's overlay and the confirmations"
  value = {
    overlay       = module.sandbox.overlay.name
    confirmations = module.sandbox.confirmations.name
  }
}

output "tools_data" {
  description = "The tools' data table and the export it was created from"
  value = {
    table = module.tools_data.table_name
    stamp = module.tools_data.stamp
    clock = module.tools_data.clock
    items = module.tools_data.items
  }
}

output "handoff" {
  description = "The handoff cases and the Lambda that files them"
  value = {
    cases_table = module.handoff.cases_table.name
    function    = module.handoff.function.name
  }
}

output "console" {
  description = "The console API, as the site serves it under /api and on its own domain, and each route's Lambda"
  value = {
    url              = "${module.site.url}/api"
    api_endpoint     = module.console.api_endpoint
    api_id           = module.console.api_id
    functions        = module.console.functions
    access_log_group = module.console.access_log_group
  }
}

output "runtime_arn" {
  value = module.runtime.runtime_arn
}

output "invoke_url" {
  value = module.runtime.invoke_url
}

output "model_key_secret" {
  description = "The secret make model-key stores the Anthropic API key in (CONTRIBUTING.md, step 3.6)"
  value       = module.runtime.model_key_secret
}

output "runtime_log_group" {
  value = module.runtime.runtime_log_group
}

output "alarms" {
  description = "The topic the alarms notify, the namespace of their metrics, and the metric filters on the Runtime's log group"
  value = {
    topic_arn      = module.alarms.topic_arn
    namespace      = module.alarms.namespace
    metric_filters = module.alarms.metric_filters
  }
}

output "runtime_tables" {
  value = module.runtime.tables
}

output "evaluation" {
  description = "The role the evaluation harness assumes, and the bucket that keeps each case's results for 90 days"
  value = {
    role_arn = module.evaluation.role_arn
    bucket   = module.evaluation.bucket
  }
}

output "suites" {
  description = "The role the integration and browser suites assume"
  value = {
    role_arn = module.suites.role_arn
  }
}

output "site" {
  description = "The site's URL, the bucket and distribution make site deploys to, and the DNS records a custom domain needs"
  value = {
    url             = module.site.url
    bucket          = module.site.bucket
    distribution_id = module.site.distribution_id
    config          = module.site.config
    domain_records  = module.site.domain_records
  }
}
