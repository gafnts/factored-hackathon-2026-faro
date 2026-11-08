output "route_key" {
  description = "The guest entry's route, which the console API's stage throttles on its own"
  value       = aws_apigatewayv2_route.entry.route_key
}

output "functions" {
  description = "The guest entry's Lambda and the cleanup's, each role named as its Lambda"
  value       = { for key, function in aws_lambda_function.function : key => function.function_name }
}

output "cleanup" {
  description = "The cleanup's Lambda, which the suites' role may run once to show its role reaches the pool"
  value = {
    name = aws_lambda_function.function["cleanup"].function_name
    arn  = aws_lambda_function.function["cleanup"].arn
  }
}

output "log_groups" {
  value = { for key, group in aws_cloudwatch_log_group.function : key => group.name }
}

output "guest_list" {
  description = "The curated customers' Parameter Store entry, which make guest-list writes"
  value       = local.guest_list
}
