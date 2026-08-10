output "alarms_topic_arn" {
  description = "Topic CloudWatch alarms publish to; the triage Lambda consumes it."
  value       = aws_sns_topic.alarms.arn
}

output "reports_topic_arn" {
  description = "Topic the triage report is emailed from."
  value       = aws_sns_topic.reports.arn
}

output "triage_function_name" {
  value = aws_lambda_function.triage.function_name
}

output "llm_secret_arn" {
  description = "Populate with: aws secretsmanager put-secret-value --secret-id <arn> --secret-string '{...}'"
  value       = aws_secretsmanager_secret.llm.arn
}

output "dedup_table_name" {
  value = aws_dynamodb_table.dedup.name
}

output "dashboard_name" {
  value = aws_cloudwatch_dashboard.main.dashboard_name
}
