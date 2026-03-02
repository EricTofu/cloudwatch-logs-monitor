variable "aws_profile" {
  description = "AWS CLI profile name for deployment"
  type        = string
}

variable "aws_region" {
  description = "AWS region"
  type        = string
  default     = "ap-northeast-1"
}

variable "schedules" {
  description = "EventBridge schedule definitions. Each schedule triggers Lambda with a list of monitor_ids."
  type = map(object({
    schedule_expression = string
    monitor_ids         = list(string)
  }))
  default = {
    "5min" = {
      schedule_expression = "rate(5 minutes)"
      monitor_ids         = ["project-a"]
    }
  }
}
