"""State transition logic for keyword monitoring."""

import logging
import time

logger = logging.getLogger(__name__)


def find_state(states, project_sk, keyword):
    """Lookup STATE record for a project#keyword combination.

    Args:
        states: List of all STATE records from DynamoDB.
        project_sk: Project sort key (e.g., "project-a").
        keyword: Keyword string (e.g., "ERROR").

    Returns:
        The matching STATE dict, or None if not found.
    """
    target_sk = f"{project_sk}#{keyword}"
    for state in states:
        if state.get("sk") == target_sk:
            return state
    return None


def resolve_renotify_min(monitor, defaults):
    """Resolve renotify_min with fallback logic.

    - Explicit value → use it
    - "disabled" → None (no re-notification)
    - Key absent → fallback to GLOBAL defaults
    """
    if "renotify_min" in monitor:
        value = monitor["renotify_min"]
        if value == "disabled":
            return None
        return value
    return defaults.get("renotify_min")


def resolve_notify_on_recover(project, defaults):
    """Resolve notify_on_recover with PROJECT → GLOBAL fallback."""
    value = project.get("notify_on_recover")
    if value is not None:
        return value
    return defaults.get("notify_on_recover", True)


def _minutes_since(epoch_ms):
    """Calculate minutes elapsed since the given epoch millisecond timestamp."""
    now_ms = int(time.time() * 1000)
    return (now_ms - epoch_ms) / (60 * 1000)


def evaluate_state(state, count, monitor, project, global_config):
    """Evaluate state transition and return the action to take.

    Args:
        state: Current STATE record (or None for first detection).
        count: Number of detected events (after exclusions).
        monitor: Monitor dict from project config.
        project: Project dict from DynamoDB.
        global_config: GLOBAL config dict from DynamoDB.

    Returns:
        One of: "NOTIFY", "RENOTIFY", "SUPPRESS",
                "RECOVER", "RECOVER_SILENT", "NOOP"
    """
    defaults = global_config.get("defaults", {})
    status = state.get("status", "OK") if state else "OK"

    renotify = resolve_renotify_min(monitor, defaults)
    notify_on_recover = resolve_notify_on_recover(project, defaults)

    if count > 0:
        if status == "OK":
            return "NOTIFY"
        elif status == "ALARM":
            last_notified = state.get("last_notified_at") if state else None
            if last_notified and renotify and _minutes_since(last_notified) >= renotify:
                return "RENOTIFY"
            return "SUPPRESS"
    else:
        if status == "ALARM":
            return "RECOVER" if notify_on_recover else "RECOVER_SILENT"
        return "NOOP"
