"""Tests for state.py — state transition logic."""

import time

from log_monitor.state import evaluate_state, resolve_notify_on_recover, resolve_renotify_min

DEFAULTS = {
    "severity": "warning",
    "renotify_min": 60,
    "notify_on_recover": True,
}
GLOBAL_CONFIG = {"defaults": DEFAULTS}


def test_resolve_renotify_min_explicit():
    assert resolve_renotify_min({"renotify_min": 30}, DEFAULTS) == 30


def test_resolve_renotify_min_disabled():
    assert resolve_renotify_min({"renotify_min": "disabled"}, DEFAULTS) is None


def test_resolve_renotify_min_fallback():
    assert resolve_renotify_min({}, DEFAULTS) == 60


def test_resolve_notify_on_recover_override():
    assert resolve_notify_on_recover({"notify_on_recover": False}, DEFAULTS) is False


def test_resolve_notify_on_recover_fallback():
    assert resolve_notify_on_recover({}, DEFAULTS) is True


def test_evaluate_notify():
    """First detection: OK → ALARM (NOTIFY)."""
    kw_config = {"severity": "critical"}
    action = evaluate_state(None, 3, kw_config, {}, GLOBAL_CONFIG)
    assert action == "NOTIFY"


def test_evaluate_notify_from_ok():
    kw_config = {"severity": "critical"}
    action = evaluate_state({"status": "OK"}, 1, kw_config, {}, GLOBAL_CONFIG)
    assert action == "NOTIFY"


def test_evaluate_suppress():
    """ALARM + detection + renotify not due → SUPPRESS."""
    now_ms = int(time.time() * 1000)
    state = {"status": "ALARM", "last_notified_at": now_ms - (10 * 60 * 1000)}
    kw_config = {"severity": "critical", "renotify_min": 60}
    action = evaluate_state(state, 2, kw_config, {}, GLOBAL_CONFIG)
    assert action == "SUPPRESS"


def test_evaluate_renotify():
    """ALARM + detection + renotify elapsed → RENOTIFY."""
    now_ms = int(time.time() * 1000)
    state = {"status": "ALARM", "last_notified_at": now_ms - (120 * 60 * 1000)}
    kw_config = {"severity": "critical", "renotify_min": 60}
    action = evaluate_state(state, 1, kw_config, {}, GLOBAL_CONFIG)
    assert action == "RENOTIFY"


def test_evaluate_suppress_disabled_renotify():
    """renotify_min=disabled → always SUPPRESS after first NOTIFY."""
    now_ms = int(time.time() * 1000)
    state = {"status": "ALARM", "last_notified_at": now_ms - (9999 * 60 * 1000)}
    kw_config = {"severity": "critical", "renotify_min": "disabled"}
    action = evaluate_state(state, 1, kw_config, {}, GLOBAL_CONFIG)
    assert action == "SUPPRESS"


def test_evaluate_recover():
    """ALARM + no detection + notify_on_recover=True → RECOVER."""
    state = {"status": "ALARM"}
    kw_config = {"severity": "critical"}
    config = {"notify_on_recover": True}
    action = evaluate_state(state, 0, kw_config, config, GLOBAL_CONFIG)
    assert action == "RECOVER"


def test_evaluate_recover_silent():
    """ALARM + no detection + notify_on_recover=False → RECOVER_SILENT."""
    state = {"status": "ALARM"}
    kw_config = {"severity": "critical"}
    config = {"notify_on_recover": False}
    action = evaluate_state(state, 0, kw_config, config, GLOBAL_CONFIG)
    assert action == "RECOVER_SILENT"


def test_evaluate_noop():
    """OK + no detection → NOOP."""
    kw_config = {"severity": "critical"}
    action = evaluate_state({"status": "OK"}, 0, kw_config, {}, GLOBAL_CONFIG)
    assert action == "NOOP"


def test_evaluate_noop_no_state():
    """No state + no detection → NOOP."""
    kw_config = {"severity": "critical"}
    action = evaluate_state(None, 0, kw_config, {}, GLOBAL_CONFIG)
    assert action == "NOOP"
