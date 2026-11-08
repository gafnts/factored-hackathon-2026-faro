output "origin_domain" {
  description = "The API's own domain, which the site's /api behavior sends requests to"
  value       = replace(aws_apigatewayv2_api.console.api_endpoint, "https://", "")
}

output "api_endpoint" {
  value = aws_apigatewayv2_api.console.api_endpoint
}

output "api_id" {
  value = aws_apigatewayv2_api.console.id
}

output "execution_arn" {
  value = aws_apigatewayv2_api.console.execution_arn
}

output "functions" {
  description = "Each route's Lambda, whose role has the Lambda's name"
  value       = { for key, function in aws_lambda_function.function : key => function.function_name }
}

output "access_log_group" {
  value = aws_cloudwatch_log_group.access.name
}
