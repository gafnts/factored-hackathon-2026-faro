output "runtime_arn" {
  value = aws_bedrockagentcore_agent_runtime.this.agent_runtime_arn
}

output "invoke_url" {
  value = "https://bedrock-agentcore.${local.region}.amazonaws.com/runtimes/${urlencode(aws_bedrockagentcore_agent_runtime.this.agent_runtime_arn)}/invocations?qualifier=DEFAULT"
}

output "model_key_secret" {
  description = "The secret make model-key stores the Anthropic API key in"
  value       = data.aws_secretsmanager_secret.model_key.name
}

output "service_name" {
  description = "The service name the Runtime's spans carry"
  value       = local.service_name
}

output "runtime_log_group" {
  value = aws_cloudwatch_log_group.runtime.name
}

output "execution_records_table" {
  value = {
    name = aws_dynamodb_table.execution_records.name
    arn  = aws_dynamodb_table.execution_records.arn
  }
}

output "tables" {
  description = "The Runtime's own tables"
  value = {
    checkpoints       = aws_dynamodb_table.checkpoints.name
    session_bindings  = aws_dynamodb_table.session_bindings.name
    execution_records = aws_dynamodb_table.execution_records.name
    usage_counters    = aws_dynamodb_table.usage_counters.name
  }
}
