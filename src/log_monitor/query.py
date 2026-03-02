"""CloudWatch Logs Insights query executor and result dispatcher."""

import logging
import time

from log_monitor.constants import POLL_INTERVAL_SEC, QUERY_TIMEOUT_SEC, get_logs_client

logger = logging.getLogger(__name__)


def execute_query(log_group, query_string, search_start_ms, search_end_ms):
    """Execute a Logs Insights query and return matching results.

    Args:
        log_group: CloudWatch log group name.
        query_string: Raw Insights query string (from DynamoDB).
        search_start_ms: Search start time in epoch milliseconds.
        search_end_ms: Search end time in epoch milliseconds.

    Returns:
        List of result rows, where each row is a list of
        {"field": name, "value": val} dicts. Returns [] on failure.
    """
    logs_client = get_logs_client()

    # Start the query
    try:
        resp = logs_client.start_query(
            logGroupName=log_group,
            startTime=search_start_ms // 1000,
            endTime=search_end_ms // 1000,
            queryString=query_string,
        )
        query_id = resp["queryId"]
    except Exception:
        logger.exception("Failed to start query: log_group=%s", log_group)
        return []

    # Poll for results
    deadline = time.time() + QUERY_TIMEOUT_SEC
    while time.time() < deadline:
        time.sleep(POLL_INTERVAL_SEC)
        try:
            result = logs_client.get_query_results(queryId=query_id)
            status = result.get("status")

            if status == "Complete":
                return result.get("results", [])
            elif status in ("Failed", "Cancelled", "Timeout"):
                logger.error("Query %s finished with status: %s", query_id, status)
                return []
            # Running/Scheduled → keep polling
        except Exception:
            logger.exception("Failed to get query results: query_id=%s", query_id)
            return []

    logger.error("Query %s timed out after %ds", query_id, QUERY_TIMEOUT_SEC)
    return []


def dispatch_results(raw_results, keywords_config):
    """Dispatch Insights results to individual keywords.

    Checks each result's @message field for keyword matches.

    Args:
        raw_results: List of result rows from Insights.
        keywords_config: List of keyword group dicts, each with a "words" list.
            If None/empty, returns {"_all": all_events} for monitor-level tracking.

    Returns:
        Dict of keyword → list of event dicts.
        Each event dict has "timestamp", "message", "log_stream" keys.
    """
    # Flatten all keywords from groups
    all_keywords = []
    if keywords_config:
        for group in keywords_config:
            for word in group.get("words", []):
                all_keywords.append(word)

    # If no keywords defined, return all results under "_all" key
    if not all_keywords:
        events = [_parse_result_row(row) for row in raw_results]
        return {"_all": events}

    dispatched = {kw: [] for kw in all_keywords}

    for row in raw_results:
        event = _parse_result_row(row)
        message = event.get("message", "")

        for keyword in all_keywords:
            if keyword in message:
                dispatched[keyword].append(event)

    return dispatched


def _parse_result_row(row):
    """Parse a single Insights result row into an event dict."""
    event = {}
    for field in row:
        name = field.get("field", "")
        value = field.get("value", "")
        if name == "@timestamp":
            event["timestamp"] = value
        elif name == "@message":
            event["message"] = value
        elif name == "@logStream":
            event["log_stream"] = value
    return event
