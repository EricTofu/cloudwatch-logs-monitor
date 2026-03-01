"""Tests for config.py — DynamoDB operations."""

import boto3
import pytest
from moto import mock_aws

from log_monitor.config import (
    get_global_config,
    merge_defaults,
    query_all_projects,
    query_all_states,
    update_project_timestamp,
    update_state,
)
from log_monitor.constants import TABLE_NAME, reset_clients
from tests.conftest import SAMPLE_GLOBAL_CONFIG, SAMPLE_PROJECT_A


@mock_aws
def test_get_global_config(aws_credentials):
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

    config = get_global_config(table)
    assert config["pk"] == "GLOBAL"
    assert config["sk"] == "CONFIG"
    assert config["source_log_group"] == "/aws/app/shared-logs"
    assert config["defaults"]["renotify_min"] == 60


@mock_aws
def test_get_global_config_missing(aws_credentials):
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

    with pytest.raises(ValueError, match="GLOBAL#CONFIG"):
        get_global_config(table)


@mock_aws
def test_query_all_projects(aws_credentials):
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
    table.put_item(Item=SAMPLE_PROJECT_A)
    table.put_item(
        Item={
            "pk": "PROJECT",
            "sk": "project-b",
            "display_name": "Project Beta",
            "enabled": True,
            "monitors": [],
        }
    )

    projects = query_all_projects(table)
    assert len(projects) == 2
    sks = {p["sk"] for p in projects}
    assert "project-a" in sks
    assert "project-b" in sks


@mock_aws
def test_query_all_states_empty(aws_credentials):
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

    states = query_all_states(table)
    assert states == []


@mock_aws
def test_update_project_timestamp(aws_credentials):
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
    table.put_item(Item=SAMPLE_PROJECT_A)

    update_project_timestamp("project-a", 1740000000000, table)

    resp = table.get_item(Key={"pk": "PROJECT", "sk": "project-a"})
    assert resp["Item"]["last_searched_at"] == 1740000000000


@mock_aws
def test_update_state_notify(aws_credentials):
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

    update_state("project-a", "ERROR", "NOTIFY", 3, 1740000000000, table)

    resp = table.get_item(Key={"pk": "STATE", "sk": "project-a#ERROR"})
    item = resp["Item"]
    assert item["status"] == "ALARM"
    assert item["last_detected_at"] == 1740000000000
    assert item["last_notified_at"] == 1740000000000
    assert item["current_streak"] == 1
    assert item["detection_count"] == 3


@mock_aws
def test_update_state_recover(aws_credentials):
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

    # First set to ALARM
    update_state("project-a", "ERROR", "NOTIFY", 3, 1740000000000, table)
    # Then recover
    update_state("project-a", "ERROR", "RECOVER", 0, 1740001000000, table)

    resp = table.get_item(Key={"pk": "STATE", "sk": "project-a#ERROR"})
    item = resp["Item"]
    assert item["status"] == "OK"
    assert item["last_detected_at"] is None
    assert item["last_notified_at"] is None
    assert item["current_streak"] == 0
    assert item["detection_count"] == 0


def test_merge_defaults():
    project = {"schedule_rate_minutes": 60, "notify_on_recover": False}
    global_config = {
        "defaults": {
            "schedule_rate_minutes": 5,
            "search_window_minutes": 5,
            "notify_on_recover": True,
            "context_lines": 5,
        }
    }

    merged = merge_defaults(project, global_config)
    assert merged["schedule_rate_minutes"] == 60  # project override
    assert merged["search_window_minutes"] == 5  # global default
    assert merged["notify_on_recover"] is False  # project override
    assert merged["context_lines"] == 5
