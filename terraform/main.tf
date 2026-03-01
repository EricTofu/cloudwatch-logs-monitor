terraform {
  required_version = ">= 1.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region  = var.aws_region
  profile = var.aws_profile
}

# ── DynamoDB Table ──
resource "aws_dynamodb_table" "log_monitor" {
  name         = var.table_name
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "pk"
  range_key    = "sk"

  attribute {
    name = "pk"
    type = "S"
  }

  attribute {
    name = "sk"
    type = "S"
  }

  tags = {
    Project = "cloudwatch-logs-monitor"
  }
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
  timeout          = var.lambda_timeout
  memory_size      = var.lambda_memory
  filename         = data.archive_file.lambda_zip.output_path
  source_code_hash = data.archive_file.lambda_zip.output_base64sha256

  environment {
    variables = {
      LOG_LEVEL  = "INFO"
      TABLE_NAME = var.table_name
    }
  }

  tags = {
    Project = "cloudwatch-logs-monitor"
  }
}

# ── IAM Role ──
resource "aws_iam_role" "lambda_role" {
  name = "cloudwatch-logs-monitor-lambda-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "lambda.amazonaws.com"
        }
      }
    ]
  })

  tags = {
    Project = "cloudwatch-logs-monitor"
  }
}

resource "aws_iam_role_policy" "lambda_policy" {
  name = "cloudwatch-logs-monitor-lambda-policy"
  role = aws_iam_role.lambda_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "CloudWatchLogs"
        Effect = "Allow"
        Action = [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents",
        ]
        Resource = "arn:aws:logs:*:*:*"
      },
      {
        Sid    = "LogsInsights"
        Effect = "Allow"
        Action = [
          "logs:StartQuery",
          "logs:GetQueryResults",
          "logs:GetLogEvents",
        ]
        Resource = "arn:aws:logs:*:*:log-group:*"
      },
      {
        Sid    = "DynamoDB"
        Effect = "Allow"
        Action = [
          "dynamodb:GetItem",
          "dynamodb:Query",
          "dynamodb:UpdateItem",
          "dynamodb:PutItem",
        ]
        Resource = aws_dynamodb_table.log_monitor.arn
      },
      {
        Sid      = "SNS"
        Effect   = "Allow"
        Action   = ["sns:Publish"]
        Resource = "arn:aws:sns:*:*:*"
      },
    ]
  })
}

# ── EventBridge Schedule ──
resource "aws_cloudwatch_event_rule" "schedule" {
  name                = "cloudwatch-logs-monitor-schedule"
  description         = "Trigger log monitor Lambda every 5 minutes"
  schedule_expression = var.schedule_rate

  tags = {
    Project = "cloudwatch-logs-monitor"
  }
}

resource "aws_cloudwatch_event_target" "lambda" {
  rule      = aws_cloudwatch_event_rule.schedule.name
  target_id = "log-monitor-lambda"
  arn       = aws_lambda_function.log_monitor.arn
}

resource "aws_lambda_permission" "eventbridge" {
  statement_id  = "AllowEventBridgeInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.log_monitor.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.schedule.arn
}
