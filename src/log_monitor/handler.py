"""Lambda handler — entry point for CloudWatch Logs Monitor."""

import logging
import time

from log_monitor.config import (
    get_global_config,
    get_monitor_config,
    get_state,
    merge_defaults,
    update_state,
    get_active_alarm_fingerprints,
)
from log_monitor.constants import INGESTION_DELAY_MIN
from log_monitor.context import enrich_with_context
from log_monitor.fingerprint import generate_fingerprint
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
            all_events = dispatched.get(keyword, [])
            
            # Fetch active alarms for this monitor/keyword
            active_fps = get_active_alarm_fingerprints(monitor_id, keyword)

            if not all_events:
                # Still need to check for RECOVER action even if 0 events
                if active_fps:
                    for fp in active_fps:
                        _evaluate_and_notify(monitor_id, config, global_config, kw_group, keyword, fp, [], now_ms)
                else:
                    _evaluate_and_notify(monitor_id, config, global_config, kw_group, keyword, None, [], now_ms)
                continue

            # Group events by fingerprint
            grouped_events = {}
            for event in all_events:
                fp = generate_fingerprint(event.get("message", ""))
                grouped_events.setdefault(fp, []).append(event)

            for fp, events in grouped_events.items():
                _evaluate_and_notify(monitor_id, config, global_config, kw_group, keyword, fp, events, now_ms)
                
            # Evaluate any active fingerprints that do not have events right now
            for fp in active_fps:
                if fp not in grouped_events:
                    _evaluate_and_notify(monitor_id, config, global_config, kw_group, keyword, fp, [], now_ms)


def _evaluate_and_notify(monitor_id, config, global_config, kw_group, keyword, fingerprint, events, now_ms):
    """Evaluate state and send notification for a specific keyword + fingerprint."""
    if events:
        events = enrich_with_context(events, config, global_config)

    count = len(events)
    state = get_state(monitor_id, keyword, fingerprint)
    action = evaluate_state(state, count, kw_group, config, global_config)

    logger.info(
        "Monitor=%s, Keyword=%s, Fingerprint=%s, Count=%d, Action=%s",
        monitor_id,
        keyword,
        fingerprint,
        count,
        action,
    )

    if action in ("NOTIFY", "RENOTIFY", "RECOVER"):
        try:
            send_notification(kw_group, config, global_config, action, events, keyword, fingerprint)
        except Exception:
            logger.exception("Failed to notify: monitor=%s, keyword=%s", monitor_id, keyword)

    if action != "NOOP":
        update_state(monitor_id, keyword, fingerprint, action, count, now_ms)


def _process_monitor_level(monitor_id, config, global_config, defaults, dispatched, now_ms):
    """Process results at monitor level (no keywords defined)."""
    all_events = dispatched.get("_all", [])
    kw_config = {"severity": config.get("severity", defaults.get("severity", "warning"))}
    keyword = "_all"
    
    active_fps = get_active_alarm_fingerprints(monitor_id, keyword)

    if not all_events:
        if active_fps:
            for fp in active_fps:
                _evaluate_and_notify(monitor_id, config, global_config, kw_config, keyword, fp, [], now_ms)
        else:
            _evaluate_and_notify(monitor_id, config, global_config, kw_config, keyword, None, [], now_ms)
        return

    # Group by fingerprint even at monitor level
    grouped_events = {}
    for event in all_events:
        fp = generate_fingerprint(event.get("message", ""))
        grouped_events.setdefault(fp, []).append(event)

    for fp, events in grouped_events.items():
        _evaluate_and_notify(monitor_id, config, global_config, kw_config, keyword, fp, events, now_ms)

    for fp in active_fps:
        if fp not in grouped_events:
            _evaluate_and_notify(monitor_id, config, global_config, kw_config, keyword, fp, [], now_ms)
