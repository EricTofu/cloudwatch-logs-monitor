"""Context line retrieval from CloudWatch Logs via GetLogEvents."""

import logging

from log_monitor.constants import get_logs_client

logger = logging.getLogger(__name__)


def get_context_lines(log_group, log_stream, timestamp_ms, num_lines=5):
    """Fetch N log lines before a given timestamp using GetLogEvents.

    Args:
        log_group: Log group name.
        log_stream: Log stream name.
        timestamp_ms: Epoch milliseconds of the detected event.
        num_lines: Number of context lines to retrieve.

    Returns:
        List of log message strings (oldest first).
    """
    if not log_stream or not timestamp_ms or num_lines <= 0:
        return []

    logs_client = get_logs_client()

    try:
        resp = logs_client.get_log_events(
            logGroupName=log_group,
            logStreamName=log_stream,
            endTime=int(timestamp_ms),
            limit=num_lines + 1,
            startFromHead=False,
        )

        events = resp.get("events", [])
        context = []
        for event in events:
            if event.get("timestamp", 0) < timestamp_ms:
                context.append(event.get("message", "").rstrip())

        return context[-num_lines:]

    except Exception:
        logger.exception(
            "Failed to get context lines: group=%s, stream=%s",
            log_group,
            log_stream,
        )
        return []


def enrich_with_context(events, monitor_config, global_config):
    """Add context_lines field to each event.

    Args:
        events: List of event dicts with "log_stream", "timestamp" fields.
        monitor_config: MONITOR config dict.
        global_config: GLOBAL config dict.

    Returns:
        The events list with "context_lines" added to each event.
    """
    defaults = global_config.get("defaults", {})
    num_lines = (
        monitor_config.get("context_lines")
        if monitor_config.get("context_lines") is not None
        else defaults.get("context_lines", 5)
    )
    log_group = monitor_config.get("log_group")

    for event in events:
        log_stream = event.get("log_stream", "")
        timestamp_str = event.get("timestamp", "")

        timestamp_ms = _parse_timestamp_ms(timestamp_str)

        if timestamp_ms and log_stream:
            event["context_lines"] = get_context_lines(
                log_group, log_stream, timestamp_ms, num_lines
            )
        else:
            event["context_lines"] = []

    return events


def _parse_timestamp_ms(timestamp_str):
    """Parse an ISO 8601 timestamp string to epoch milliseconds."""
    if not timestamp_str:
        return None

    from datetime import datetime, timezone

    try:
        for fmt in (
            "%Y-%m-%d %H:%M:%S.%f",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%dT%H:%M:%S.%fZ",
            "%Y-%m-%dT%H:%M:%SZ",
        ):
            try:
                dt = datetime.strptime(timestamp_str, fmt).replace(tzinfo=timezone.utc)
                return int(dt.timestamp() * 1000)
            except ValueError:
                continue
        logger.warning("Could not parse timestamp: %s", timestamp_str)
        return None
    except Exception:
        logger.warning("Error parsing timestamp: %s", timestamp_str)
        return None
