"""Shared test fixtures for moto-based AWS mocking."""

import os

import boto3
import pytest
from moto import mock_aws

from log_monitor.constants import TABLE_NAME, reset_clients

os.environ.setdefault("AWS_DEFAULT_REGION", "ap-northeast-1")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")


@pytest.fixture
def aws_credentials():
    """Ensure dummy AWS credentials for moto."""
    os.environ["AWS_ACCESS_KEY_ID"] = "testing"
    os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
    os.environ["AWS_SECURITY_TOKEN"] = "testing"
    os.environ["AWS_SESSION_TOKEN"] = "testing"
    os.environ["AWS_DEFAULT_REGION"] = "ap-northeast-1"


@pytest.fixture
def dynamodb_table(aws_credentials):
    """Create a moto DynamoDB table with seeded data."""
    with mock_aws():
        reset_clients()
        dynamodb = boto3.resource("dynamodb", region_name="ap-northeast-1")
        table = dynamodb.create_table(
            TableName=TABLE_NAME,
            KeySchema=[
                {"AttributeName": "pk", "KeyType": "HASH"},
                {"AttributeName": "sk", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "pk", "AttributeType": "S"},
                {"AttributeName": "sk", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        table.put_item(Item=SAMPLE_GLOBAL_CONFIG)
        table.put_item(Item=SAMPLE_MONITOR_A)
        table.put_item(Item=SAMPLE_MONITOR_DAILY)
        yield table
        reset_clients()


# ── Sample Data ──

SAMPLE_GLOBAL_CONFIG = {
    "pk": "GLOBAL",
    "sk": "CONFIG",
    "defaults": {
        "severity": "warning",
        "search_window_minutes": 7,
        "context_lines": 5,
        "renotify_min": 60,
        "notify_on_recover": True,
    },
    "sns_topics": {
        "critical": "arn:aws:sns:ap-northeast-1:123456789012:slack-critical",
        "warning": "arn:aws:sns:ap-northeast-1:123456789012:slack-warning",
    },
    "ses_config": {
        "from_address": "alerts@example.com",
        "reply_to": ["admin@example.com"],
        "recipients": {
            "critical": ["oncall@example.com", "manager@example.com"],
            "warning": ["team@example.com"],
        },
    },
    "notification_template": {
        "subject": "[{severity}] {display_name} - {keyword} 検出",
        "body": "{keyword} detected {count} times\n{log_lines}",
    },
    "recover_template": {
        "subject": "[RECOVER] {display_name} - {keyword} 復旧",
        "body": "復旧: {display_name} {keyword}",
    },
}

SAMPLE_MONITOR_A = {
    "pk": "MONITOR",
    "sk": "project-a",
    "display_name": "Project Alpha",
    "log_group": "/aws/app/shared-logs",
    "search_window_minutes": 7,
    "context_lines": 10,
    "query": (
        "fields @timestamp, @message, @logStream\n"
        "| filter @logStream like /project-a/\n"
        "| filter (@message like /ERROR/ or @message like /FATAL/ or @message like /TIMEOUT/)\n"
        "| sort @timestamp asc\n"
        "| limit 500"
    ),
    "keywords": [
        {
            "words": ["ERROR", "FATAL"],
            "severity": "critical",
            "renotify_min": 30,
        },
        {
            "words": ["TIMEOUT"],
            "severity": "warning",
            "renotify_min": "disabled",
        },
    ],
    "notify_on_recover": True,
    "enabled": True,
}

SAMPLE_MONITOR_DAILY = {
    "pk": "MONITOR",
    "sk": "project-c-daily",
    "display_name": "Project Charlie Daily",
    "log_group": "/aws/app/project-c",
    "search_window_minutes": 1450,
    "query": (
        "fields @timestamp, @message, @logStream\n"
        "| filter @message like /ERROR|WARN/\n"
        "| sort @timestamp asc\n"
        "| limit 1000"
    ),
    "severity": "info",
    "notify_on_recover": False,
    "enabled": True,
}
