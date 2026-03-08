"""Lambda handler — entry point for CloudWatch Logs Monitor."""

import logging
import time

from log_monitor.config import (
    get_active_alarm_fingerprints,
    get_global_config,
    get_monitor_config,
    get_state,
    merge_defaults,
    update_state,
)
from log_monitor.constants import INGESTION_DELAY_MIN
from log_monitor.context import enrich_with_context
from log_monitor.fingerprint import generate_fingerprint
from log_monitor.notifier import send_notification
from log_monitor.query import dispatch_results, execute_query, poll_queries, start_query
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

    # 1. Start queries for all monitors in parallel
    pending_queries = {}

    for monitor_id in monitor_ids:
        try:
            config = get_monitor_config(monitor_id)
            if not config:
                logger.warning("Monitor config not found: %s", monitor_id)
                continue

            if not config.get("enabled", True):
                logger.info("Monitor disabled: %s", monitor_id)
                continue

            defaults = merge_defaults(config, global_config)
            search_window_ms = defaults["search_window_minutes"] * 60 * 1000
            search_start_ms = search_end_ms - search_window_ms

            query_string = config.get("query")
            if not query_string:
                logger.warning("No query defined for monitor: %s", monitor_id)
                continue

            log_group = config.get("log_group")
            if not log_group:
                logger.warning("No log_group defined for monitor: %s", monitor_id)
                continue

            logger.info(
                "Starting query for %s: window=%dmin, log_group=%s",
                monitor_id,
                defaults["search_window_minutes"],
                log_group,
            )

            query_id = start_query(log_group, query_string, search_start_ms, search_end_ms)
            if query_id:
                pending_queries[query_id] = {
                    "monitor_id": monitor_id,
                    "config": config,
                    "defaults": defaults,
                }
        except Exception:
            logger.exception("Failed to initialize monitor: %s", monitor_id)

    if not pending_queries:
        logger.info("No queries started, exiting.")
        return

    # 2. Poll all queries
    logger.info("Polling %d queries...", len(pending_queries))
    completed_results = poll_queries(list(pending_queries.keys()))

    # 3. Process results for each monitor
    for query_id, raw_results in completed_results.items():
        monitor_info = pending_queries[query_id]
        monitor_id = monitor_info["monitor_id"]
        config = monitor_info["config"]
        defaults = monitor_info["defaults"]

        try:
            process_monitor_results(monitor_id, config, global_config, defaults, raw_results, now_ms)
        except Exception:
            logger.exception("Failed to process results for monitor: %s", monitor_id)


def process_monitor(monitor_id, global_config, search_end_ms, now_ms):
    """Process a single monitor synchronously.
    Mainly kept for compatibility with existing tests."""
    config = get_monitor_config(monitor_id)
    if not config or not config.get("enabled", True):
        return

    defaults = merge_defaults(config, global_config)
    search_window_ms = defaults["search_window_minutes"] * 60 * 1000
    search_start_ms = search_end_ms - search_window_ms

    query_string = config.get("query")
    log_group = config.get("log_group")
    if not query_string or not log_group:
        return

    raw_results = execute_query(log_group, query_string, search_start_ms, search_end_ms)
    process_monitor_results(monitor_id, config, global_config, defaults, raw_results, now_ms)


def process_monitor_results(monitor_id, config, global_config, defaults, raw_results, now_ms):
    """Process raw Insights results for a monitor."""
    keywords_config = config.get("keywords")
    dispatched = dispatch_results(raw_results, keywords_config)

    if keywords_config:
        _process_keywords(monitor_id, config, global_config, dispatched, now_ms)
    else:
        _process_monitor_level(monitor_id, config, global_config, defaults, dispatched, now_ms)


def _process_keyword_events(monitor_id, config, global_config, kw_config, keyword, all_events, now_ms):
    """Common logic to group by fingerprint, evaluate state, and notify for a specific keyword."""
    active_fps = get_active_alarm_fingerprints(monitor_id, keyword)

    aggregated_events = {"NOTIFY": [], "RECOVER": []}

    if not all_events:
        if active_fps:
            for fp in active_fps:
                _evaluate_and_aggregate(
                    monitor_id, config, global_config, kw_config, keyword, fp, [], now_ms, aggregated_events
                )
        else:
            _evaluate_and_aggregate(
                monitor_id, config, global_config, kw_config, keyword, None, [], now_ms, aggregated_events
            )
    else:
        grouped_events = {}
        for event in all_events:
            fp = generate_fingerprint(event.get("message", ""))
            grouped_events.setdefault(fp, []).append(event)

        for fp, events in grouped_events.items():
            _evaluate_and_aggregate(
                monitor_id, config, global_config, kw_config, keyword, fp, events, now_ms, aggregated_events
            )

        for fp in active_fps:
            if fp not in grouped_events:
                _evaluate_and_aggregate(
                    monitor_id, config, global_config, kw_config, keyword, fp, [], now_ms, aggregated_events
                )

    _notify_aggregated(monitor_id, config, global_config, kw_config, keyword, aggregated_events)


def _process_keywords(monitor_id, config, global_config, dispatched, now_ms):
    """Process dispatched results per keyword group."""
    for kw_group in config.get("keywords", []):
        for keyword in kw_group.get("words", []):
            all_events = dispatched.get(keyword, [])
            _process_keyword_events(monitor_id, config, global_config, kw_group, keyword, all_events, now_ms)


def _process_monitor_level(monitor_id, config, global_config, defaults, dispatched, now_ms):
    """Process results at monitor level (no keywords defined)."""
    all_events = dispatched.get("_all", [])
    kw_config = {"severity": config.get("severity", defaults.get("severity", "warning"))}

    _process_keyword_events(monitor_id, config, global_config, kw_config, "_all", all_events, now_ms)


def _evaluate_and_aggregate(
    monitor_id, config, global_config, kw_group, keyword, fingerprint, events, now_ms, aggregated_events
):
    """Evaluate state and add to aggregated events WITHOUT notifying yet."""
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

    original_message = events[0].get("message", "(No message extracted)") if events else None

    stored_original_message = state.get("original_message") if state else None

    msg_to_store = original_message
    if action == "RECOVER" or (action in ("RENOTIFY", "SUPPRESS") and not original_message):
        msg_to_store = stored_original_message

    if action in ("NOTIFY", "RENOTIFY", "RECOVER"):
        category = "NOTIFY" if action in ("NOTIFY", "RENOTIFY") else "RECOVER"

        for event in events:
            event["_fingerprint"] = fingerprint
            event["_original_message"] = msg_to_store
            aggregated_events[category].append(event)

        if action == "RECOVER" and not events:
            aggregated_events[category].append(
                {
                    "message": f"Recovered: {msg_to_store}",
                    "timestamp": "",
                    "_fingerprint": fingerprint,
                    "_original_message": msg_to_store,
                }
            )

    if action != "NOOP":
        update_state(monitor_id, keyword, fingerprint, action, count, now_ms, msg_to_store)


def _notify_aggregated(monitor_id, config, global_config, kw_group, keyword, aggregated_events):
    """Send consolidated notifications for grouped categories."""
    for category, events in aggregated_events.items():
        if not events:
            continue

        try:
            unique_fps = list(dict.fromkeys(e.get("_fingerprint", "") for e in events if e.get("_fingerprint")))
            fp_str = ", ".join(unique_fps)

            # Combine multiple original messages if present
            unique_oms = list(
                dict.fromkeys(e.get("_original_message", "") for e in events if e.get("_original_message"))
            )
            om_str = "\n---\n".join(unique_oms) if unique_oms else ""

            send_notification(kw_group, config, global_config, category, events, keyword, fp_str, om_str)
        except Exception:
            logger.exception(
                "Failed to notify aggregated: monitor=%s, keyword=%s, category=%s", monitor_id, keyword, category
            )
