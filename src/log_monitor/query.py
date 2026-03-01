"""CloudWatch Logs Insights query builder and executor."""

import logging
import re
import time

from log_monitor.constants import (
    BATCH_SIZE,
    DEFAULT_QUERY_LIMIT,
    POLL_INTERVAL_SEC,
    QUERY_TIMEOUT_SEC,
    get_logs_client,
)

logger = logging.getLogger(__name__)

# Regex metacharacters (for deciding query-side vs app-side exclusion)
_REGEX_META = re.compile(r'[\\.*+?^${}()|[\]]')


def build_combined_query(project, global_config):
    """Build a Logs Insights query combining all keywords from all monitors.

    Includes:
    - log_stream_pattern filter (if set)
    - All keywords from all monitors (OR-combined)
    - Simple exclusion patterns in the query (non-regex only)
    """
    parts = ["fields @timestamp, @message, @logStream"]

    # Stream pattern filter
    stream_pattern = project.get("log_stream_pattern")
    if stream_pattern:
        parts.append(f"| filter @logStream like /{stream_pattern}/")

    # Collect all keywords from all monitors
    all_keywords = []
    for monitor in project.get("monitors", []):
        all_keywords.extend(monitor.get("keywords", []))

    if not all_keywords:
        logger.warning("Project %s has no keywords configured", project.get("sk"))
        return None

    # Build keyword filter
    keyword_conditions = [f"@message like /{kw}/" for kw in all_keywords]
    if len(keyword_conditions) == 1:
        parts.append(f"| filter {keyword_conditions[0]}")
    else:
        joined = " or ".join(keyword_conditions)
        parts.append(f"| filter ({joined})")

    # Simple exclusion patterns (non-regex only, applied at query level)
    project_excludes = project.get("exclude_patterns", [])
    for pattern in project_excludes:
        if not _REGEX_META.search(pattern):
            # Escape forward slashes for Insights regex syntax
            safe = pattern.replace("/", "\\/")
            parts.append(f"| filter @message not like /{safe}/")

    parts.append("| sort @timestamp asc")

    # Scale limit based on keyword count
    limit = min(DEFAULT_QUERY_LIMIT * max(1, len(all_keywords) // 3), 10000)
    parts.append(f"| limit {limit}")

    return "\n".join(parts)


def start_all_queries(active_projects, global_config, search_start_ms, search_end_ms):
    """Fire start_query for all projects. Returns {query_id: project}."""
    logs_client = get_logs_client()
    pending = {}

    for project in active_projects:
        query_string = build_combined_query(project, global_config)
        if query_string is None:
            continue

        log_group = project.get("override_log_group") or global_config.get("source_log_group")
        if not log_group:
            logger.error("No log group for project %s", project.get("sk"))
            continue

        try:
            resp = logs_client.start_query(
                logGroupName=log_group,
                startTime=search_start_ms // 1000,
                endTime=search_end_ms // 1000,
                queryString=query_string,
            )
            pending[resp["queryId"]] = project
            logger.info("Started query for project %s: %s", project.get("sk"), resp["queryId"])
        except Exception:
            logger.exception("Failed to start query for project %s", project.get("sk"))

    return pending


def poll_all_queries(pending):
    """Poll get_query_results until all complete or timeout.

    Returns {project_sk: {"project": project, "results": [...]}}
    """
    logs_client = get_logs_client()
    completed = {}
    start_time = time.time()

    while pending:
        if time.time() - start_time > QUERY_TIMEOUT_SEC:
            for qid, proj in pending.items():
                logger.error("Query timeout: project=%s, query_id=%s", proj["sk"], qid)
            break

        time.sleep(POLL_INTERVAL_SEC)

        for query_id in list(pending.keys()):
            try:
                resp = logs_client.get_query_results(queryId=query_id)
                status = resp["status"]

                if status == "Complete":
                    project = pending[query_id]
                    completed[project["sk"]] = {
                        "project": project,
                        "results": resp.get("results", []),
                    }
                    del pending[query_id]
                    logger.info(
                        "Query complete: project=%s, results=%d",
                        project["sk"],
                        len(resp.get("results", [])),
                    )
                elif status in ("Failed", "Cancelled", "Timeout"):
                    logger.error(
                        "Query %s for project %s: %s",
                        query_id,
                        pending[query_id]["sk"],
                        status,
                    )
                    del pending[query_id]
                # Running, Scheduled → next poll
            except Exception:
                logger.exception("Error polling query %s", query_id)

    return completed


def execute_all_queries(active_projects, global_config, search_start_ms, search_end_ms):
    """Orchestrator: start_all_queries → poll_all_queries with batch splitting."""
    all_completed = {}

    for i in range(0, len(active_projects), BATCH_SIZE):
        batch = active_projects[i : i + BATCH_SIZE]
        pending = start_all_queries(batch, global_config, search_start_ms, search_end_ms)
        completed = poll_all_queries(pending)
        all_completed.update(completed)

    return all_completed


def dispatch_results(results, monitors):
    """Distribute Insights query results to individual keywords.

    Args:
        results: List of Insights result rows. Each row is a list of
                 {"field": name, "value": value} dicts.
        monitors: List of monitor dicts from the project config.

    Returns:
        {keyword: [{"timestamp": ..., "message": ..., "log_stream": ...}, ...]}
    """
    # Flatten all keywords from all monitors
    all_keywords = []
    for monitor in monitors:
        all_keywords.extend(monitor.get("keywords", []))

    dispatched = {kw: [] for kw in all_keywords}

    for row in results:
        # Parse the row into a dict
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

        message = event.get("message", "")

        # Match against keywords (a message can match multiple keywords)
        for kw in all_keywords:
            if kw in message:
                dispatched[kw].append(event)

    return dispatched
