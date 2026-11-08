output "url" {
  value = "https://${var.attach_domain ? var.domain_name : aws_cloudfront_distribution.site.domain_name}"
}

output "domain_records" {
  description = "The CNAMEs domain_name needs in its DNS: the certificate's validation, kept for renewals, and the name itself"
  value = var.domain_name == null ? [] : concat(
    [for option in aws_acm_certificate.site[0].domain_validation_options : {
      name  = option.resource_record_name
      type  = option.resource_record_type
      value = option.resource_record_value
    }],
    [{ name = var.domain_name, type = "CNAME", value = aws_cloudfront_distribution.site.domain_name }],
  )
}

output "bucket" {
  value = aws_s3_bucket.site.id
}

output "distribution_id" {
  value = aws_cloudfront_distribution.site.id
}

output "config" {
  description = "What config.json holds, for the development server"
  value       = var.config
}

output "access_log_group" {
  value = aws_cloudwatch_log_group.access.name
}

output "access_delivery" {
  description = "The delivery of the site's access logs, which the suites read to check its fields"
  value = {
    id  = aws_cloudwatch_log_delivery.access.id
    arn = aws_cloudwatch_log_delivery.access.arn
  }
}
