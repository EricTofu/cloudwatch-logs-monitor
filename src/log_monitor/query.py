"""CloudWatch Logs Insights query executor and result dispatcher."""

import logging
import time

from log_monitor.constants import POLL_INTERVAL_SEC, QUERY_TIMEOUT_SEC, get_logs_client

logger = logging.getLogger(__name__)


def start_query(log_group, query_string, search_start_ms, search_end_ms):
    """Start a Logs Insights query and return its queryId.

    Args:
        log_group: CloudWatch log group name.
        query_string: Raw Insights query string.
        search_start_ms: Search start time in epoch milliseconds.
        search_end_ms: Search end time in epoch milliseconds.

    Returns:
        queryId (str) on success, or None on failure.
    """
    logs_client = get_logs_client()
    try:
        resp = logs_client.start_query(
            logGroupName=log_group,
            startTime=search_start_ms // 1000,
            endTime=search_end_ms // 1000,
            queryString=query_string,
        )
        return resp["queryId"]
    except Exception:
        logger.exception("Failed to start query: log_group=%s", log_group)
        return None


def poll_queries(pending_queries):
    """Poll multiple Logs Insights queries until they complete or timeout.

    Args:
        pending_queries: A list of queryIds to poll.

    Returns:
        A dict of queryId -> list of result rows (same format as execute_query).
        Queries that fail or timeout will return an empty list [].
    """
    logs_client = get_logs_client()
    results = {query_id: [] for query_id in pending_queries}
    active_queries = set(pending_queries)
    deadline = time.time() + QUERY_TIMEOUT_SEC

    while active_queries and time.time() < deadline:
        time.sleep(POLL_INTERVAL_SEC)

        # We need to copy the set because we might modify it during iteration
        for query_id in list(active_queries):
            try:
                result = logs_client.get_query_results(queryId=query_id)
                status = result.get("status")

                if status == "Complete":
                    results[query_id] = result.get("results", [])
                    active_queries.remove(query_id)
                elif status in ("Failed", "Cancelled", "Timeout"):
                    logger.error("Query %s finished with status: %s", query_id, status)
                    active_queries.remove(query_id)
                # Running/Scheduled → keep polling
            except Exception:
                logger.exception("Failed to get query results: query_id=%s", query_id)
                active_queries.remove(query_id)

    if active_queries:
        for qid in active_queries:
            logger.error("Query %s timed out after %ds", qid, QUERY_TIMEOUT_SEC)

    return results


def execute_query(log_group, query_string, search_start_ms, search_end_ms):
    """Execute a Logs Insights query and return matching results.

    For backwards compatibility or single executions.
    """
    query_id = start_query(log_group, query_string, search_start_ms, search_end_ms)
    if not query_id:
        return []

    results = poll_queries([query_id])
    return results.get(query_id, [])


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
