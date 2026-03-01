"""Lambda handler — entry point for CloudWatch Logs Monitor."""

import logging
import time

from log_monitor.config import (
    get_global_config,
    merge_defaults,
    query_all_projects,
    query_all_states,
    update_project_timestamp,
    update_state,
)
from log_monitor.constants import INGESTION_DELAY_MIN
from log_monitor.context import enrich_with_context
from log_monitor.exclusion import apply_exclusions
from log_monitor.notifier import send_notification
from log_monitor.query import dispatch_results, execute_all_queries
from log_monitor.state import evaluate_state, find_state

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def handler(event, context):
    """Lambda entry point. Invoked by EventBridge every 5 minutes."""
    logger.info("Starting log monitor execution")

    # 1. Load all config from DynamoDB
    global_config = get_global_config()
    projects = query_all_projects()
    states = query_all_states()

    # Search end time: now - ingestion delay buffer
    now_ms = int(time.time() * 1000)
    search_end_ms = now_ms - (INGESTION_DELAY_MIN * 60 * 1000)

    # 2. Filter active projects and check schedules
    active_projects = []
    merged_defaults = {}
    for project in projects:
        if not project.get("enabled", True):
            logger.info("Skipping disabled project: %s", project.get("sk"))
            continue

        defaults = merge_defaults(project, global_config)
        merged_defaults[project["sk"]] = defaults

        if should_skip_project(project, search_end_ms, defaults):
            logger.info("Skipping project (schedule): %s", project.get("sk"))
            continue

        active_projects.append(project)

    if not active_projects:
        logger.info("No active projects to process")
        return

    logger.info("Processing %d active projects", len(active_projects))

    # 3. Determine search start per project and execute queries
    project_search_starts = {}
    for project in active_projects:
        defaults = merged_defaults[project["sk"]]
        search_window_ms = defaults["search_window_minutes"] * 60 * 1000
        last_searched = project.get("last_searched_at")

        if last_searched and isinstance(last_searched, (int, float)):
            project_search_starts[project["sk"]] = int(last_searched)
        else:
            project_search_starts[project["sk"]] = search_end_ms - search_window_ms

    # Execute Insights queries (async, batched)
    query_results = execute_all_queries(
        active_projects,
        global_config,
        min(project_search_starts.values()),  # earliest start across all projects
        search_end_ms,
    )

    # 4. Process each project's results
    for project in active_projects:
        project_sk = project["sk"]
        try:
            result_data = query_results.get(project_sk)
            if result_data is None:
                logger.warning("No query results for project %s (query may have failed)", project_sk)
                # Still update timestamp so we don't re-search the same window
                update_project_timestamp(project_sk, search_end_ms)
                continue

            process_project(project, result_data["results"], states, global_config, now_ms)
            update_project_timestamp(project_sk, search_end_ms)

        except Exception:
            logger.exception("Failed to process project %s", project_sk)
            continue

    logger.info("Log monitor execution complete")


def should_skip_project(project, search_end_ms, defaults):
    """Check if project should be skipped based on schedule_rate_minutes."""
    schedule_rate = defaults.get("schedule_rate_minutes", 5)
    last_searched = project.get("last_searched_at")

    if not last_searched or not isinstance(last_searched, (int, float)):
        return False  # First run, don't skip

    elapsed_ms = search_end_ms - int(last_searched)
    threshold_ms = (schedule_rate - 1) * 60 * 1000  # 1-minute buffer for jitter

    return elapsed_ms < threshold_ms


def process_project(project, raw_results, states, global_config, now_ms):
    """Process a single project's query results.

    Steps:
    1. Dispatch results to individual keywords
    2. For each keyword: exclude → evaluate state → notify → update state
    """
    monitors = project.get("monitors", [])
    project_sk = project["sk"]

    # Dispatch Insights results to individual keywords
    dispatched = dispatch_results(raw_results, monitors)

    # Process each monitor and its keywords
    for monitor in monitors:
        keywords = monitor.get("keywords", [])
        project_excludes = project.get("exclude_patterns", [])
        monitor_excludes = monitor.get("exclude_patterns", [])

        for keyword in keywords:
            events = dispatched.get(keyword, [])

            # Apply exclusion patterns (app-level regex only; simple ones were in query)
            events = apply_exclusions(events, project_excludes, monitor_excludes)

            # Enrich with context lines (only if there are events and context is configured)
            if events:
                events = enrich_with_context(events, project, global_config)

            # Evaluate state transition
            count = len(events)
            state = find_state(states, project_sk, keyword)
            action = evaluate_state(state, count, monitor, project, global_config)

            logger.info(
                "Project=%s, Keyword=%s, Count=%d, Action=%s",
                project_sk,
                keyword,
                count,
                action,
            )

            # Send notification if needed
            if action in ("NOTIFY", "RENOTIFY", "RECOVER"):
                try:
                    send_notification(monitor, project, global_config, action, events, keyword)
                except Exception:
                    logger.exception(
                        "Failed to send notification: project=%s, keyword=%s",
                        project_sk,
                        keyword,
                    )

            # Update STATE in DynamoDB
            if action != "NOOP":
                update_state(project_sk, keyword, action, count, now_ms)
