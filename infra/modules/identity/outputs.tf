output "user_pool_id" {
  value = aws_cognito_user_pool.this.id
}

output "user_pool_arn" {
  value = aws_cognito_user_pool.this.arn
}

output "customer_client_id" {
  value = aws_cognito_user_pool_client.this["customers"].id
}

output "staff_client_id" {
  value = aws_cognito_user_pool_client.this["staff"].id
}

output "issuer" {
  value = "https://${aws_cognito_user_pool.this.endpoint}"
}

output "discovery_url" {
  value = "https://${aws_cognito_user_pool.this.endpoint}/.well-known/openid-configuration"
}

output "staff_allowlist" {
  description = "The staff allowlist's path in Parameter Store: an entry per staff user, named by its sub"
  value       = local.staff_allowlist
}
