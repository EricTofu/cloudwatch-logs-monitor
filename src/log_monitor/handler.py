"""Lambda handler — entry point for CloudWatch Logs Monitor."""

import logging
import time

from log_monitor.config import get_global_config, get_monitor_config, get_state, merge_defaults, update_state
from log_monitor.constants import INGESTION_DELAY_MIN
from log_monitor.context import enrich_with_context
from log_monitor.notifier import send_notification
from log_monitor.query import dispatch_results, execute_query
from log_monitor.state import evaluate_state

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def handler(event, context):
    """Lambda entry point. Invoked by EventBridge with monitor_ids.

    Expected event format:
        {"monitor_ids": ["project-a", "project-b"]}
    """
    monitor_ids = event.get("monitor_ids", [])
    if not monitor_ids:
        logger.warning("No monitor_ids in event")
        return

    logger.info("Processing %d monitors: %s", len(monitor_ids), monitor_ids)

    global_config = get_global_config()
    now_ms = int(time.time() * 1000)
    search_end_ms = now_ms - (INGESTION_DELAY_MIN * 60 * 1000)

    for monitor_id in monitor_ids:
        try:
            process_monitor(monitor_id, global_config, search_end_ms, now_ms)
        except Exception:
            logger.exception("Failed to process monitor: %s", monitor_id)


def process_monitor(monitor_id, global_config, search_end_ms, now_ms):
    """Process a single monitor: query → dispatch → evaluate → notify.

    Args:
        monitor_id: MONITOR sort key in DynamoDB.
        global_config: GLOBAL config dict.
        search_end_ms: Search end time (epoch ms).
        now_ms: Current time (epoch ms).
    """
    config = get_monitor_config(monitor_id)
    if not config:
        logger.warning("Monitor config not found: %s", monitor_id)
        return

    if not config.get("enabled", True):
        logger.info("Monitor disabled: %s", monitor_id)
        return

    defaults = merge_defaults(config, global_config)

    # Calculate search window
    search_window_ms = defaults["search_window_minutes"] * 60 * 1000
    search_start_ms = search_end_ms - search_window_ms

    # 1. Execute query (raw query from DynamoDB)
    query_string = config.get("query")
    if not query_string:
        logger.warning("No query defined for monitor: %s", monitor_id)
        return

    log_group = config.get("log_group")
    if not log_group:
        logger.warning("No log_group defined for monitor: %s", monitor_id)
        return

    logger.info(
        "Executing query for %s: window=%dmin, log_group=%s",
        monitor_id,
        defaults["search_window_minutes"],
        log_group,
    )

    raw_results = execute_query(log_group, query_string, search_start_ms, search_end_ms)

    # 2. Dispatch results to keywords
    keywords_config = config.get("keywords")
    dispatched = dispatch_results(raw_results, keywords_config)

    # 3. Process each keyword (or _all for monitor-level)
    if keywords_config:
        _process_keywords(monitor_id, config, global_config, defaults, dispatched, now_ms)
    else:
        _process_monitor_level(monitor_id, config, global_config, defaults, dispatched, now_ms)


def _process_keywords(monitor_id, config, global_config, defaults, dispatched, now_ms):
    """Process dispatched results per keyword group."""
    for kw_group in config.get("keywords", []):
        for keyword in kw_group.get("words", []):
            events = dispatched.get(keyword, [])

            # Enrich with context lines
            if events:
                events = enrich_with_context(events, config, global_config)

            # Evaluate state transition
            count = len(events)
            state = get_state(monitor_id, keyword)
            action = evaluate_state(state, count, kw_group, config, global_config)

            logger.info(
                "Monitor=%s, Keyword=%s, Count=%d, Action=%s",
                monitor_id,
                keyword,
                count,
                action,
            )

            # Send notification
            if action in ("NOTIFY", "RENOTIFY", "RECOVER"):
                try:
                    send_notification(kw_group, config, global_config, action, events, keyword)
                except Exception:
                    logger.exception("Failed to notify: monitor=%s, keyword=%s", monitor_id, keyword)

            # Update STATE
            if action != "NOOP":
                update_state(monitor_id, keyword, action, count, now_ms)


def _process_monitor_level(monitor_id, config, global_config, defaults, dispatched, now_ms):
    """Process results at monitor level (no keywords defined)."""
    events = dispatched.get("_all", [])

    if events:
        events = enrich_with_context(events, config, global_config)

    count = len(events)
    # Use a synthetic kw_config from monitor-level settings
    kw_config = {"severity": config.get("severity", defaults.get("severity", "warning"))}
    state = get_state(monitor_id, "_all")
    action = evaluate_state(state, count, kw_config, config, global_config)

    logger.info("Monitor=%s, Count=%d, Action=%s", monitor_id, count, action)

    if action in ("NOTIFY", "RENOTIFY", "RECOVER"):
        try:
            send_notification(kw_config, config, global_config, action, events, monitor_id)
        except Exception:
            logger.exception("Failed to notify: monitor=%s", monitor_id)

    if action != "NOOP":
        update_state(monitor_id, "_all", action, count, now_ms)
