terraform {
  required_version = ">= 1.0"
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 5.0" }
  }
}

provider "aws" {
  region  = var.aws_region
  profile = var.aws_profile
}

# ── DynamoDB Table ──
resource "aws_dynamodb_table" "log_monitor" {
  name         = "cloudwatch-logs-monitor"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "pk"
  range_key    = "sk"

  attribute { name = "pk"; type = "S" }
  attribute { name = "sk"; type = "S" }
}

# ── Lambda Function ──
data "archive_file" "lambda_zip" {
  type        = "zip"
  source_dir  = "${path.module}/../src"
  output_path = "${path.module}/.build/lambda.zip"
}

resource "aws_lambda_function" "log_monitor" {
  function_name    = "cloudwatch-logs-monitor"
  role             = aws_iam_role.lambda_role.arn
  handler          = "log_monitor.handler.handler"
  runtime          = "python3.12"
  timeout          = 300
  memory_size      = 256
  filename         = data.archive_file.lambda_zip.output_path
  source_code_hash = data.archive_file.lambda_zip.output_base64sha256

  environment {
    variables = {
      LOG_LEVEL = "INFO"
    }
  }
}

# ── EventBridge Schedules (per schedule group) ──
resource "aws_cloudwatch_event_rule" "schedule" {
  for_each            = var.schedules
  name                = "cloudwatch-logs-monitor-${each.key}"
  schedule_expression = each.value.schedule_expression
}

resource "aws_cloudwatch_event_target" "schedule" {
  for_each  = var.schedules
  rule      = aws_cloudwatch_event_rule.schedule[each.key].name
  target_id = "cloudwatch-logs-monitor-${each.key}"
  arn       = aws_lambda_function.log_monitor.arn
  input     = jsonencode({ monitor_ids = each.value.monitor_ids })
}

resource "aws_lambda_permission" "eventbridge" {
  for_each      = var.schedules
  statement_id  = "AllowEventBridge${each.key}"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.log_monitor.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.schedule[each.key].arn
}

# ── IAM Role ──
resource "aws_iam_role" "lambda_role" {
  name = "cloudwatch-logs-monitor-lambda-role"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action    = "sts:AssumeRole"
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
    }]
  })
}

resource "aws_iam_role_policy" "lambda_policy" {
  name = "cloudwatch-logs-monitor-lambda-policy"
  role = aws_iam_role.lambda_role.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents",
        ]
        Resource = "arn:aws:logs:*:*:*"
      },
      {
        Effect = "Allow"
        Action = [
          "logs:StartQuery",
          "logs:GetQueryResults",
          "logs:GetLogEvents",
        ]
        Resource = "*"
      },
      {
        Effect   = "Allow"
        Action   = ["dynamodb:GetItem", "dynamodb:UpdateItem"]
        Resource = aws_dynamodb_table.log_monitor.arn
      },
      {
        Effect   = "Allow"
        Action   = ["sns:Publish"]
        Resource = "*"
      },
      {
        Effect   = "Allow"
        Action   = ["ses:SendEmail", "ses:SendRawEmail"]
        Resource = "*"
      },
    ]
  })
}
