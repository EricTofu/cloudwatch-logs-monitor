"""Shared constants and cached AWS clients."""

from datetime import timedelta, timezone

import boto3
from botocore.config import Config

# ── DynamoDB ──
TABLE_NAME = "cloudwatch-logs-monitor"

# ── Timezone ──
JST = timezone(timedelta(hours=9))

# ── Logs Insights ──
INGESTION_DELAY_MIN = 2
POLL_INTERVAL_SEC = 1
QUERY_TIMEOUT_SEC = 120

# ── SNS ──
MAX_MESSAGE_BYTES = 256 * 1024  # 256KB

# ── Cached boto3 clients (initialized once per Lambda cold start) ──
_boto_config = Config(retries={"mode": "standard", "max_attempts": 5})

_logs_client = None
_sns_client = None
_ses_client = None
_dynamodb_resource = None


def get_logs_client():
    global _logs_client
    if _logs_client is None:
        _logs_client = boto3.client("logs", config=_boto_config)
    return _logs_client


def get_sns_client():
    global _sns_client
    if _sns_client is None:
        _sns_client = boto3.client("sns", config=_boto_config)
    return _sns_client


def get_ses_client():
    global _ses_client
    if _ses_client is None:
        _ses_client = boto3.client("ses", config=_boto_config)
    return _ses_client


def get_dynamodb_resource():
    global _dynamodb_resource
    if _dynamodb_resource is None:
        _dynamodb_resource = boto3.resource("dynamodb")
    return _dynamodb_resource


def reset_clients():
    """Reset cached clients. Used in tests."""
    global _logs_client, _sns_client, _ses_client, _dynamodb_resource
    _logs_client = None
    _sns_client = None
    _ses_client = None
    _dynamodb_resource = None
