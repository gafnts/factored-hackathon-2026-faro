output "topic_arn" {
  description = "The topic every alarm notifies, subscribed by hand (CONTRIBUTING.md)"
  value       = aws_sns_topic.alarms.arn
}

output "namespace" {
  description = "The namespace of the metrics the filters publish"
  value       = local.namespace
}

output "metric_filters" {
  description = "Each metric filter's name, by its metric"
  value = merge(
    { for metric, filter in aws_cloudwatch_log_metric_filter.this : metric => filter.name },
    { for metric, filter in aws_cloudwatch_log_metric_filter.guest : metric => filter.name },
  )
}
