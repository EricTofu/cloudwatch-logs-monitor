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

            # Map to aggregate by category: "NOTIFY" (NOTIFY + RENOTIFY) and "RECOVER"
            aggregated_events = {
                "NOTIFY": [],
                "RECOVER": []
            }

            if not all_events:
                # Still need to check for RECOVER action even if 0 events
                if active_fps:
                    for fp in active_fps:
                        _evaluate_and_aggregate(monitor_id, config, global_config, kw_group, keyword, fp, [], now_ms, aggregated_events)
                else:
                    _evaluate_and_aggregate(monitor_id, config, global_config, kw_group, keyword, None, [], now_ms, aggregated_events)
            else:
                # Group events by fingerprint
                grouped_events = {}
                for event in all_events:
                    fp = generate_fingerprint(event.get("message", ""))
                    grouped_events.setdefault(fp, []).append(event)

                for fp, events in grouped_events.items():
                    _evaluate_and_aggregate(monitor_id, config, global_config, kw_group, keyword, fp, events, now_ms, aggregated_events)

                # Evaluate any active fingerprints that do not have events right now
                for fp in active_fps:
                    if fp not in grouped_events:
                        _evaluate_and_aggregate(monitor_id, config, global_config, kw_group, keyword, fp, [], now_ms, aggregated_events)

            _notify_aggregated(monitor_id, config, global_config, kw_group, keyword, aggregated_events)

def _evaluate_and_aggregate(monitor_id, config, global_config, kw_group, keyword, fingerprint, events, now_ms, aggregated_events):
    """Evaluate state and add to aggregated events WITHOUT notifying yet."""
    if events:
        events = enrich_with_context(events, config, global_config)

    count = len(events)
    state = get_state(monitor_id, keyword, fingerprint)
    action = evaluate_state(state, count, kw_group, config, global_config)

    logger.info(
        "Monitor=%s, Keyword=%s, Fingerprint=%s, Count=%d, Action=%s",
        monitor_id, keyword, fingerprint, count, action,
    )

    original_message = events[0].get("message", "(No message extracted)") if events else None

    if action in ("NOTIFY", "RENOTIFY", "RECOVER"):
        stored_original_message = state.get("original_message") if state else None
        if action == "NOTIFY" and original_message:
            stored_original_message = original_message

        # Determine category for aggregation
        category = "NOTIFY" if action in ("NOTIFY", "RENOTIFY") else "RECOVER"

        # We store the events, but we also want to keep track of the original_message if needed.
        # Since events might be multiple, we append them to the category's event list.
        # However, to preserve fingerprint/original_message context in the notification (if desired),
        # we can attach them to the events or handle them in the notifier. The current
        # notifier expects events, fingerprint(string), original_message(string).
        # We will bundle them in the events list as custom fields, or just pass a combined string.

        for event in events:
            # We want each event to carry its fingerprint and original_message for debugging/context
            event["_fingerprint"] = fingerprint
            event["_original_message"] = original_message
            aggregated_events[category].append(event)

        # For RECOVER, there are usually 0 events, but we still need to send the notification
        # containing the original message that recovered.
        if action == "RECOVER" and not events:
            # Create a "dummy" event to carry the original message so it shows up in context
            aggregated_events[category].append({
                "message": f"Recovered: {original_message}",
                "timestamp": "",
                "_fingerprint": fingerprint,
                "_original_message": original_message
            })

    if action != "NOOP":
        update_state(monitor_id, keyword, fingerprint, action, count, now_ms, original_message)

def _notify_aggregated(monitor_id, config, global_config, kw_group, keyword, aggregated_events):
    """Send consolidated notifications for grouped categories."""
    for category, events in aggregated_events.items():
        if not events:
            continue

        try:
            # We pass multiple fingerprints/original_messages combined or just omit them
            # if the notifier relies primarily on events for the body.
            # Let's collect unique fingerprints and original messages to pass along.
            unique_fps = list(dict.fromkeys(e.get("_fingerprint", "") for e in events if e.get("_fingerprint")))
            fp_str = ", ".join(unique_fps)

            # Use the first original message or combine them if needed
            om_str = events[0].get("_original_message", "") if events else ""

            # The action passed to `send_notification` determines the template, so
            # category "NOTIFY" uses notification_template, "RECOVER" uses recover_template.
            send_notification(kw_group, config, global_config, category, events, keyword, fp_str, om_str)
        except Exception:
            logger.exception("Failed to notify aggregated: monitor=%s, keyword=%s, category=%s", monitor_id, keyword, category)

def _process_monitor_level(monitor_id, config, global_config, defaults, dispatched, now_ms):
    """Process results at monitor level (no keywords defined)."""
    all_events = dispatched.get("_all", [])
    kw_config = {"severity": config.get("severity", defaults.get("severity", "warning"))}
    keyword = "_all"

    active_fps = get_active_alarm_fingerprints(monitor_id, keyword)

    aggregated_events = {
        "NOTIFY": [],
        "RECOVER": []
    }

    if not all_events:
        if active_fps:
            for fp in active_fps:
                _evaluate_and_aggregate(monitor_id, config, global_config, kw_config, keyword, fp, [], now_ms, aggregated_events)
        else:
            _evaluate_and_aggregate(monitor_id, config, global_config, kw_config, keyword, None, [], now_ms, aggregated_events)
    else:
        # Group by fingerprint even at monitor level
        grouped_events = {}
        for event in all_events:
            fp = generate_fingerprint(event.get("message", ""))
            grouped_events.setdefault(fp, []).append(event)

        for fp, events in grouped_events.items():
            _evaluate_and_aggregate(monitor_id, config, global_config, kw_config, keyword, fp, events, now_ms, aggregated_events)

        for fp in active_fps:
            if fp not in grouped_events:
                _evaluate_and_aggregate(monitor_id, config, global_config, kw_config, keyword, fp, [], now_ms, aggregated_events)

    _notify_aggregated(monitor_id, config, global_config, kw_config, keyword, aggregated_events)
