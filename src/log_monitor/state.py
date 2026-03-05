"""State transition logic for monitor keyword tracking."""

import logging
import time

logger = logging.getLogger(__name__)


def resolve_renotify_min(kw_config, monitor_config, defaults):
    """Resolve renotify_min with fallback logic.

    - Keyword config → use it
    - "disabled" → None (no re-notification)
    - Key absent → fallback to MONITOR config
    - Key absent → fallback to GLOBAL defaults
    """
    if "renotify_min" in kw_config:
        value = kw_config["renotify_min"]
        if value == "disabled":
            return None
        return value
    if "renotify_min" in monitor_config:
        value = monitor_config["renotify_min"]
        if value == "disabled":
            return None
        return value
    return defaults.get("renotify_min")


def resolve_notify_on_recover(config, defaults):
    """Resolve notify_on_recover with MONITOR → GLOBAL fallback."""
    value = config.get("notify_on_recover")
    if value is not None:
        return value
    return defaults.get("notify_on_recover", True)


def _minutes_since(epoch_ms):
    """Calculate minutes elapsed since the given epoch millisecond timestamp."""
    now_ms = int(time.time() * 1000)
    return (now_ms - epoch_ms) / (60 * 1000)


def evaluate_state(state, count, kw_config, monitor_config, global_config):
    """Evaluate state transition and return the action to take.

    Args:
        state: Current STATE record (or None for first detection).
        count: Number of detected events for this keyword.
        kw_config: Keyword group config dict (has renotify_min, severity).
        monitor_config: MONITOR config dict (has notify_on_recover).
        global_config: GLOBAL config dict.

    Returns:
        One of: "NOTIFY", "RENOTIFY", "SUPPRESS",
                "RECOVER", "RECOVER_SILENT", "NOOP"
    """
    defaults = global_config.get("defaults", {})
    status = state.get("status", "OK") if state else "OK"

    renotify = resolve_renotify_min(kw_config, monitor_config, defaults)
    notify_on_recover = resolve_notify_on_recover(monitor_config, defaults)

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
