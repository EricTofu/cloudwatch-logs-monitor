"""Context line retrieval from CloudWatch Logs via GetLogEvents."""

import logging

from log_monitor.constants import get_logs_client

logger = logging.getLogger(__name__)


# Module-level cache for memoization during a single Lambda execution
_context_cache = {}


def get_context_lines(log_group, log_stream, timestamp_ms, num_lines=5):
    """Fetch log lines around a given timestamp using GetLogEvents.

    Uses overlapping time windows to ensure logs at the exact same
    millisecond are not excluded. Returns a simple time-ordered block
    of context without attempting to pinpoint the exact target line.

    Results are memoized per (log_stream, timestamp_ms) to avoid redundant
    API calls when the same log line matches multiple keywords.

    Args:
        log_group: Log group name.
        log_stream: Log stream name.
        timestamp_ms: Epoch milliseconds of the detected event.
        num_lines: Number of context lines to retrieve (each way).

    Returns:
        List of log message strings.
    """
    if not log_stream or not timestamp_ms or num_lines <= 0:
        return []

    cache_key = f"{log_stream}|{timestamp_ms}"
    if cache_key in _context_cache:
        return _context_cache[cache_key]

    logs_client = get_logs_client()

    try:
        # Fetch BEFORE and INCLUDING the target event
        # endTime is exclusive in get_log_events, so +1 to include timestamp_ms
        resp_before = logs_client.get_log_events(
            logGroupName=log_group,
            logStreamName=log_stream,
            endTime=int(timestamp_ms) + 1,
            limit=num_lines + 10,
            startFromHead=False,
        )
        before_events = [
            (e.get("timestamp", 0), e.get("message", "").rstrip())
            for e in resp_before.get("events", [])
        ]

        # Fetch AFTER and INCLUDING the target event
        resp_after = logs_client.get_log_events(
            logGroupName=log_group,
            logStreamName=log_stream,
            startTime=int(timestamp_ms),
            limit=num_lines + 10,
            startFromHead=True,
        )
        after_events = [
            (e.get("timestamp", 0), e.get("message", "").rstrip())
            for e in resp_after.get("events", [])
        ]

        # Merge and deduplicate by (timestamp, message)
        seen = set()
        merged = []
        for ts, msg in before_events + after_events:
            key = (ts, msg)
            if key not in seen:
                seen.add(key)
                merged.append((ts, msg))

        # Sort by timestamp
        merged.sort(key=lambda x: x[0])

        # Extract just the message strings
        context = [msg for _, msg in merged]

        _context_cache[cache_key] = context
        return context

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
