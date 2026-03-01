variable "aws_profile" {
  description = "AWS CLI profile name for deployment"
  type        = string
}

variable "aws_region" {
  description = "AWS region"
  type        = string
  default     = "ap-northeast-1"
}

variable "lambda_timeout" {
  description = "Lambda function timeout in seconds"
  type        = number
  default     = 600 # 10 minutes
}

variable "lambda_memory" {
  description = "Lambda function memory in MB"
  type        = number
  default     = 512
}

variable "schedule_rate" {
  description = "EventBridge schedule rate"
  type        = string
  default     = "rate(5 minutes)"
}

variable "table_name" {
  description = "DynamoDB table name"
  type        = string
  default     = "log-monitor"
}
