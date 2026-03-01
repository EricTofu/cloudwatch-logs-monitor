"""Shared test fixtures for moto-based AWS mocking."""

import os

import boto3
import pytest
from moto import mock_aws

from log_monitor.constants import TABLE_NAME, reset_clients

# Force boto3 to use us-east-1 in tests (moto default)
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
    """Create a moto DynamoDB table with seeded GLOBAL config."""
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

        # Seed GLOBAL config
        table.put_item(Item=SAMPLE_GLOBAL_CONFIG)
        # Seed sample projects
        table.put_item(Item=SAMPLE_PROJECT_A)
        table.put_item(Item=SAMPLE_PROJECT_B)

        yield table
        reset_clients()


@pytest.fixture
def sns_topics(aws_credentials):
    """Create moto SNS topics and return their ARNs."""
    with mock_aws():
        sns = boto3.client("sns", region_name="ap-northeast-1")
        topics = {}
        for name in ["slack-critical", "slack-warning", "slack-info", "email-critical", "email-alerts"]:
            resp = sns.create_topic(Name=name)
            topics[name] = resp["TopicArn"]
        yield topics


# ── Sample Data ──

SAMPLE_GLOBAL_CONFIG = {
    "pk": "GLOBAL",
    "sk": "CONFIG",
    "source_log_group": "/aws/app/shared-logs",
    "defaults": {
        "severity": "warning",
        "search_window_minutes": 5,
        "schedule_rate_minutes": 5,
        "renotify_min": 60,
        "notify_on_recover": True,
        "context_lines": 5,
    },
    "sns_topics": {
        "critical": "arn:aws:sns:ap-northeast-1:123456789012:slack-critical",
        "warning": "arn:aws:sns:ap-northeast-1:123456789012:slack-warning",
        "info": "arn:aws:sns:ap-northeast-1:123456789012:slack-info",
    },
    "email_sns_topics": {
        "critical": "arn:aws:sns:ap-northeast-1:123456789012:email-critical",
        "warning": "arn:aws:sns:ap-northeast-1:123456789012:email-alerts",
    },
    "notification_template": {
        "subject": "[{severity}] {project} - {keyword} 検出",
        "body": "{severity} {project} {keyword} {count}件 {log_lines}",
    },
    "recover_template": {
        "subject": "[RECOVER] {project} - {keyword} 復旧",
        "body": "復旧: {project} {keyword}",
    },
}

SAMPLE_PROJECT_A = {
    "pk": "PROJECT",
    "sk": "project-a",
    "display_name": "Project Alpha",
    "log_stream_pattern": "project-a",
    "enabled": True,
    "exclude_patterns": ["healthcheck"],
    "monitors": [
        {
            "keywords": ["ERROR", "FATAL"],
            "severity": "critical",
            "exclude_patterns": ["connection reset"],
            "renotify_min": 30,
        },
        {
            "keywords": ["TIMEOUT"],
            "severity": "warning",
            "renotify_min": "disabled",
        },
    ],
}

SAMPLE_PROJECT_B = {
    "pk": "PROJECT",
    "sk": "project-b",
    "display_name": "Project Beta",
    "override_log_group": "/aws/app/project-b",
    "enabled": True,
    "monitors": [
        {
            "keywords": ["ERROR"],
            "severity": "critical",
        },
    ],
}

SAMPLE_STATE_ALARM = {
    "pk": "STATE",
    "sk": "project-a#ERROR",
    "status": "ALARM",
    "last_detected_at": 1740000000000,
    "last_notified_at": 1740000000000,
    "detection_count": 5,
    "current_streak": 2,
}

SAMPLE_STATE_OK = {
    "pk": "STATE",
    "sk": "project-a#TIMEOUT",
    "status": "OK",
    "last_detected_at": None,
    "last_notified_at": None,
    "detection_count": 0,
    "current_streak": 0,
}
