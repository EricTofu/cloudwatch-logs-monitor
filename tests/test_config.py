"""Tests for config.py — DynamoDB operations."""

import boto3
import pytest
from moto import mock_aws

from log_monitor.config import (
    get_global_config,
    get_monitor_config,
    get_state,
    merge_defaults,
    update_state,
)
from log_monitor.constants import TABLE_NAME, reset_clients
from tests.conftest import SAMPLE_GLOBAL_CONFIG, SAMPLE_MONITOR_A


def _create_table():
    dynamodb = boto3.resource("dynamodb", region_name="ap-northeast-1")
    return dynamodb.create_table(
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


@mock_aws
def test_get_global_config(aws_credentials):
    reset_clients()
    table = _create_table()
    table.put_item(Item=SAMPLE_GLOBAL_CONFIG)

    config = get_global_config(table)
    assert config["pk"] == "GLOBAL"
    assert config["defaults"]["renotify_min"] == 60


@mock_aws
def test_get_global_config_missing(aws_credentials):
    reset_clients()
    table = _create_table()

    with pytest.raises(ValueError, match="GLOBAL#CONFIG"):
        get_global_config(table)


@mock_aws
def test_get_monitor_config(aws_credentials):
    reset_clients()
    table = _create_table()
    table.put_item(Item=SAMPLE_MONITOR_A)

    config = get_monitor_config("project-a", table)
    assert config["display_name"] == "Project Alpha"
    assert config["log_group"] == "/aws/app/shared-logs"
    assert len(config["keywords"]) == 2


@mock_aws
def test_get_monitor_config_missing(aws_credentials):
    reset_clients()
    table = _create_table()

    config = get_monitor_config("nonexistent", table)
    assert config is None


@mock_aws
def test_get_state_missing(aws_credentials):
    reset_clients()
    table = _create_table()

    state = get_state("project-a", "ERROR", table)
    assert state is None


@mock_aws
def test_update_state_notify(aws_credentials):
    reset_clients()
    table = _create_table()

    update_state("project-a", "ERROR", None, "NOTIFY", 3, 1740000000000, table)

    state = get_state("project-a", "ERROR", None, table)
    assert state["status"] == "ALARM"
    assert state["last_detected_at"] == 1740000000000
    assert state["detection_count"] == 3
    assert state["current_streak"] == 1


@mock_aws
def test_update_state_recover(aws_credentials):
    reset_clients()
    table = _create_table()

    update_state("project-a", "ERROR", None, "NOTIFY", 3, 1740000000000, table)
    update_state("project-a", "ERROR", None, "RECOVER", 0, 1740001000000, table)

    state = get_state("project-a", "ERROR", None, table)
    assert state["status"] == "OK"
    assert state["last_detected_at"] is None
    assert state["current_streak"] == 0


@mock_aws
def test_update_state_renotify(aws_credentials):
    reset_clients()
    table = _create_table()

    update_state("project-a", "ERROR", None, "NOTIFY", 2, 1740000000000, table)
    update_state("project-a", "ERROR", None, "RENOTIFY", 1, 1740001000000, table)

    state = get_state("project-a", "ERROR", None, table)
    assert state["status"] == "ALARM"
    assert state["last_notified_at"] == 1740001000000
    assert state["detection_count"] == 3  # 2 + 1
    assert state["current_streak"] == 2  # 1 + 1


def test_merge_defaults():
    config = {"search_window_minutes": 1450, "notify_on_recover": False}
    global_config = {
        "defaults": {
            "search_window_minutes": 7,
            "context_lines": 5,
            "renotify_min": 60,
            "notify_on_recover": True,
            "severity": "warning",
        }
    }
    merged = merge_defaults(config, global_config)
    assert merged["search_window_minutes"] == 1450  # monitor override
    assert merged["context_lines"] == 5  # global default
    assert merged["notify_on_recover"] is False  # monitor override
    assert merged["renotify_min"] == 60
