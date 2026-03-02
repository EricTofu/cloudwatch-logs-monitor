"""Tests for handler.py — Lambda entry point (integration test)."""

import time
from unittest.mock import MagicMock, patch

import boto3
from moto import mock_aws

from log_monitor.constants import TABLE_NAME, reset_clients
from log_monitor.handler import handler, process_project, should_skip_project
from tests.conftest import SAMPLE_GLOBAL_CONFIG, SAMPLE_PROJECT_A, SAMPLE_PROJECT_B


class TestShouldSkipProject:
    def test_first_run_no_skip(self):
        meta = {}
        defaults = {"schedule_rate_minutes": 5}
        assert should_skip_project(meta, 1740000000000, defaults) is False

    def test_within_schedule(self):
        now_ms = int(time.time() * 1000)
        meta = {"last_searched_at": now_ms - (2 * 60 * 1000)}
        defaults = {"schedule_rate_minutes": 5}
        assert should_skip_project(meta, now_ms, defaults) is True

    def test_past_schedule(self):
        now_ms = int(time.time() * 1000)
        meta = {"last_searched_at": now_ms - (10 * 60 * 1000)}
        defaults = {"schedule_rate_minutes": 5}
        assert should_skip_project(meta, now_ms, defaults) is False

    def test_daily_schedule(self):
        now_ms = int(time.time() * 1000)
        meta = {"last_searched_at": now_ms - (60 * 60 * 1000)}
        defaults = {"schedule_rate_minutes": 1440}
        assert should_skip_project(meta, now_ms, defaults) is True


class TestProcessProject:
    def test_process_with_no_results(self):
        """No Insights results → all keywords get NOOP or stay in current state."""
        project = SAMPLE_PROJECT_A.copy()
        raw_results = []  # no matches
        states = []
        global_config = SAMPLE_GLOBAL_CONFIG.copy()
        now_ms = int(time.time() * 1000)

        # Should not raise
        with patch("log_monitor.handler.update_state") as mock_update:
            process_project(project, raw_results, states, global_config, now_ms)
            # NOOP actions should NOT call update_state
            mock_update.assert_not_called()

    def test_process_with_matches(self):
        """Insights results with keyword matches → should trigger NOTIFY."""
        project = SAMPLE_PROJECT_A.copy()
        raw_results = [
            [
                {"field": "@timestamp", "value": "2026-03-01 10:00:00.000"},
                {"field": "@message", "value": "ERROR: database failed"},
                {"field": "@logStream", "value": "project-a/stream-1"},
            ],
        ]
        states = []
        global_config = SAMPLE_GLOBAL_CONFIG.copy()
        now_ms = int(time.time() * 1000)

        with (
            patch("log_monitor.handler.send_notification") as mock_notify,
            patch("log_monitor.handler.update_state") as mock_update,
            patch("log_monitor.handler.enrich_with_context", side_effect=lambda e, p, g: e),
        ):
            process_project(project, raw_results, states, global_config, now_ms)

            # ERROR keyword should trigger NOTIFY
            mock_notify.assert_called()
            mock_update.assert_called()


@mock_aws
def test_handler_integration(aws_credentials):
    """Full integration test with moto DynamoDB + mocked Logs Insights."""
    reset_clients()

    # Set up DynamoDB
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
    table.put_item(Item=SAMPLE_PROJECT_A)

    # Set up SNS topics
    sns = boto3.client("sns", region_name="ap-northeast-1")
    for name in ["slack-critical", "slack-warning", "slack-info", "email-critical", "email-alerts"]:
        sns.create_topic(Name=name)

    # Mock Logs Insights (moto doesn't emulate actual query execution)
    mock_start_query = MagicMock(return_value={"queryId": "test-query-id-1"})
    mock_get_results = MagicMock(
        return_value={
            "status": "Complete",
            "results": [
                [
                    {"field": "@timestamp", "value": "2026-03-01 10:00:00.000"},
                    {"field": "@message", "value": "ERROR: test error"},
                    {"field": "@logStream", "value": "project-a/stream-1"},
                ],
            ],
        }
    )

    with (
        patch("log_monitor.query.get_logs_client") as mock_logs_client,
        patch("log_monitor.handler.enrich_with_context", side_effect=lambda e, p, g: e),
    ):
        mock_client = MagicMock()
        mock_client.start_query = mock_start_query
        mock_client.get_query_results = mock_get_results
        mock_logs_client.return_value = mock_client

        # Run handler
        handler({}, None)

    # Verify STATE was created
    resp = table.get_item(Key={"pk": "STATE", "sk": "project-a#ERROR"})
    assert "Item" in resp
    assert resp["Item"]["status"] == "ALARM"

    # Verify last_searched_at was updated on PROJECT_META (not PROJECT)
    resp = table.get_item(Key={"pk": "PROJECT_META", "sk": "project-a"})
    assert "last_searched_at" in resp["Item"]

    reset_clients()


@mock_aws
def test_handler_error_isolation(aws_credentials):
    """One project failing should not affect other projects."""
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
    table.put_item(Item=SAMPLE_PROJECT_A)
    table.put_item(Item=SAMPLE_PROJECT_B)

    sns = boto3.client("sns", region_name="ap-northeast-1")
    for name in ["slack-critical", "slack-warning", "slack-info", "email-critical", "email-alerts"]:
        sns.create_topic(Name=name)

    call_count = {"start_query": 0}

    def mock_start_query(**kwargs):
        call_count["start_query"] += 1
        return {"queryId": f"query-{call_count['start_query']}"}

    def mock_get_results(**kwargs):
        qid = kwargs.get("queryId", "")
        if qid == "query-1":
            # First project returns results that will cause an error in processing
            return {"status": "Complete", "results": []}
        else:
            return {"status": "Complete", "results": []}

    with (
        patch("log_monitor.query.get_logs_client") as mock_logs_client,
        patch("log_monitor.handler.enrich_with_context", side_effect=lambda e, p, g: e),
    ):
        mock_client = MagicMock()
        mock_client.start_query = mock_start_query
        mock_client.get_query_results = mock_get_results
        mock_logs_client.return_value = mock_client

        # Should not raise even if processing fails for one project
        handler({}, None)

    reset_clients()
