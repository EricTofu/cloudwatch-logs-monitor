output "lambda_function_name" {
  value = aws_lambda_function.log_monitor.function_name
}

output "lambda_function_arn" {
  value = aws_lambda_function.log_monitor.arn
}

output "dynamodb_table_name" {
  value = aws_dynamodb_table.log_monitor.name
}

output "dynamodb_table_arn" {
  value = aws_dynamodb_table.log_monitor.arn
}

output "eventbridge_rule_name" {
  value = aws_cloudwatch_event_rule.schedule.name
}
