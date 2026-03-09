"""Tests for handler.py — Lambda entry point (integration test)."""

from unittest.mock import MagicMock, patch

import boto3
from moto import mock_aws

from log_monitor.constants import TABLE_NAME, reset_clients
from log_monitor.handler import handler, process_monitor
from tests.conftest import SAMPLE_GLOBAL_CONFIG, SAMPLE_MONITOR_A, SAMPLE_MONITOR_DAILY


class TestProcessMonitor:
    def test_no_results(self):
        """No Insights results → all keywords get NOOP."""
        with (
            patch("log_monitor.handler.get_monitor_config", return_value=SAMPLE_MONITOR_A),
            patch("log_monitor.handler.get_global_config", return_value=SAMPLE_GLOBAL_CONFIG),
            patch("log_monitor.handler.execute_query", return_value=[]),
            patch("log_monitor.handler.update_state") as mock_update,
            patch("log_monitor.handler.get_state", return_value=None),
            patch("log_monitor.handler.get_active_alarm_fingerprints", return_value=[]),
        ):
            process_monitor("project-a", SAMPLE_GLOBAL_CONFIG, 1740000000000, 1740000000000)
            mock_update.assert_not_called()

    def test_with_matches(self):
        """Results with keyword matches → should trigger NOTIFY."""
        raw_results = [
            [
                {"field": "@timestamp", "value": "2026-03-01 10:00:00.000"},
                {"field": "@message", "value": "ERROR: database failed"},
                {"field": "@logStream", "value": "project-a/stream-1"},
            ],
        ]
        with (
            patch("log_monitor.handler.get_monitor_config", return_value=SAMPLE_MONITOR_A),
            patch("log_monitor.handler.execute_query", return_value=raw_results),
            patch("log_monitor.handler.send_notification") as mock_notify,
            patch("log_monitor.handler.update_state") as mock_update,
            patch("log_monitor.handler.get_state", return_value=None),
            patch("log_monitor.handler.get_active_alarm_fingerprints", return_value=[]),
            patch("log_monitor.handler.enrich_with_context", side_effect=lambda e, c, g: e),
        ):
            process_monitor("project-a", SAMPLE_GLOBAL_CONFIG, 1740000000000, 1740000000000)
            mock_notify.assert_called()
            mock_update.assert_called()

    def test_monitor_level_no_keywords(self):
        """Monitor without keywords → _all tracking."""
        raw_results = [
            [
                {"field": "@timestamp", "value": "2026-03-01 10:00:00.000"},
                {"field": "@message", "value": "ERROR in daily scan"},
                {"field": "@logStream", "value": "stream-1"},
            ],
        ]
        with (
            patch("log_monitor.handler.get_monitor_config", return_value=SAMPLE_MONITOR_DAILY),
            patch("log_monitor.handler.execute_query", return_value=raw_results),
            patch("log_monitor.handler.send_notification") as mock_notify,
            patch("log_monitor.handler.update_state") as mock_update,
            patch("log_monitor.handler.get_state", return_value=None),
            patch("log_monitor.handler.get_active_alarm_fingerprints", return_value=[]),
            patch("log_monitor.handler.enrich_with_context", side_effect=lambda e, c, g: e),
        ):
            process_monitor("project-c-daily", SAMPLE_GLOBAL_CONFIG, 1740000000000, 1740000000000)
            mock_notify.assert_called()
            # STATE should use "_all" as keyword
            mock_update.assert_called_once()
            call_args = mock_update.call_args
            assert call_args[0][0] == "project-c-daily"  # monitor_id
            assert call_args[0][1] == "_all"  # keyword

    def test_disabled_monitor(self):
        """Disabled monitor should be skipped."""
        disabled_config = {**SAMPLE_MONITOR_A, "enabled": False}
        with (
            patch("log_monitor.handler.get_monitor_config", return_value=disabled_config),
            patch("log_monitor.handler.execute_query") as mock_query,
        ):
            process_monitor("project-a", SAMPLE_GLOBAL_CONFIG, 1740000000000, 1740000000000)
            mock_query.assert_not_called()


@mock_aws
def test_handler_integration(aws_credentials):
    """Full integration test with moto DynamoDB + mocked Logs Insights."""
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

    # Set up SNS topics
    sns = boto3.client("sns", region_name="ap-northeast-1")
    for name in ["slack-critical", "slack-warning", "email-critical", "email-alerts"]:
        sns.create_topic(Name=name)

    # Set up SES verified email
    ses = boto3.client("ses", region_name="ap-northeast-1")
    ses.verify_email_identity(EmailAddress="alerts@example.com")

    mock_start_query = MagicMock(return_value={"queryId": "test-query-id"})
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
        patch("log_monitor.handler.enrich_with_context", side_effect=lambda e, c, g: e),
    ):
        mock_client = MagicMock()
        mock_client.start_query = mock_start_query
        mock_client.get_query_results = mock_get_results
        mock_logs_client.return_value = mock_client

        handler({"monitor_ids": ["project-a"]}, None)

    # Verify STATE was created for ERROR keyword + fingerprint
    from log_monitor.fingerprint import generate_fingerprint

    fp = generate_fingerprint("ERROR: test error")

    resp = table.get_item(Key={"pk": "STATE", "sk": f"project-a#ERROR#{fp}"})
    assert "Item" in resp
    assert resp["Item"]["status"] == "ALARM"

    reset_clients()


@mock_aws
def test_handler_error_isolation(aws_credentials):
    """One monitor failing should not affect others."""
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

    with (
        patch("log_monitor.handler.start_query", side_effect=["qid1", "qid2"]),
        patch("log_monitor.handler.poll_queries", return_value={"qid1": [], "qid2": []}),
        patch("log_monitor.handler.process_monitor_results") as mock_process,
    ):
        mock_process.side_effect = [Exception("boom"), None]
        # Should not raise even if first monitor fails
        handler({"monitor_ids": ["project-a", "project-c-daily"]}, None)
        assert mock_process.call_count == 2

    reset_clients()


def test_handler_empty_event():
    """No monitor_ids → early return."""
    handler({}, None)  # should not raise


@mock_aws
def test_recovery_with_fingerprints(aws_credentials):
    """Test that active alarms with fingerprints are recovered when 0 events occur."""
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

    # Pre-seed an ALARM state for a specific fingerprint
    now_ms = 1740000000000
    table.put_item(
        Item={
            "pk": "STATE",
            "sk": "project-a#ERROR#test_fp_123",
            "status": "ALARM",
            "last_detected_at": now_ms - 300000,
            "last_notified_at": now_ms - 300000,
            "current_streak": 5,
            "detection_count": 10,
        }
    )

    # Mock getting 0 results
    with (
        patch("log_monitor.handler.get_monitor_config", return_value=SAMPLE_MONITOR_A),
        patch("log_monitor.handler.get_global_config", return_value=SAMPLE_GLOBAL_CONFIG),
        patch("log_monitor.handler.execute_query", return_value=[]),
        patch("log_monitor.handler.send_notification") as mock_notify,
    ):
        process_monitor("project-a", SAMPLE_GLOBAL_CONFIG, now_ms, now_ms)

        # Notify should have been called for RECOVER
        mock_notify.assert_called_once()
        call_args = mock_notify.call_args[0]
        assert call_args[3] == "RECOVER"
        assert "test_fp_123" in call_args[6]

        # Check DynamoDB that state is now OK
        resp = table.get_item(Key={"pk": "STATE", "sk": "project-a#ERROR#test_fp_123"})
        assert resp["Item"]["status"] == "OK"

    reset_clients()
