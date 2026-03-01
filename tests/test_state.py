"""Tests for state.py — state transition logic."""

import time

from log_monitor.state import (
    evaluate_state,
    find_state,
    resolve_notify_on_recover,
    resolve_renotify_min,
)

DEFAULTS = {
    "severity": "warning",
    "renotify_min": 60,
    "notify_on_recover": True,
}

GLOBAL_CONFIG = {"defaults": DEFAULTS}


def test_find_state_exists():
    states = [
        {"sk": "project-a#ERROR", "status": "ALARM"},
        {"sk": "project-a#TIMEOUT", "status": "OK"},
    ]
    result = find_state(states, "project-a", "ERROR")
    assert result["status"] == "ALARM"


def test_find_state_not_found():
    states = [{"sk": "project-a#ERROR", "status": "ALARM"}]
    result = find_state(states, "project-a", "MISSING")
    assert result is None


def test_resolve_renotify_min_explicit():
    monitor = {"renotify_min": 30}
    assert resolve_renotify_min(monitor, DEFAULTS) == 30


def test_resolve_renotify_min_disabled():
    monitor = {"renotify_min": "disabled"}
    assert resolve_renotify_min(monitor, DEFAULTS) is None


def test_resolve_renotify_min_fallback():
    monitor = {}
    assert resolve_renotify_min(monitor, DEFAULTS) == 60


def test_resolve_notify_on_recover_project_override():
    project = {"notify_on_recover": False}
    assert resolve_notify_on_recover(project, DEFAULTS) is False


def test_resolve_notify_on_recover_fallback():
    project = {}
    assert resolve_notify_on_recover(project, DEFAULTS) is True


def test_evaluate_state_notify():
    """First detection: OK → ALARM (NOTIFY)."""
    state = None
    monitor = {"keywords": ["ERROR"], "severity": "critical"}
    project = {}
    action = evaluate_state(state, 3, monitor, project, GLOBAL_CONFIG)
    assert action == "NOTIFY"


def test_evaluate_state_notify_with_ok_state():
    """Explicit OK state + detection → NOTIFY."""
    state = {"status": "OK"}
    monitor = {"keywords": ["ERROR"], "severity": "critical"}
    project = {}
    action = evaluate_state(state, 1, monitor, project, GLOBAL_CONFIG)
    assert action == "NOTIFY"


def test_evaluate_state_suppress():
    """ALARM + detection + renotify not yet due → SUPPRESS."""
    now_ms = int(time.time() * 1000)
    state = {
        "status": "ALARM",
        "last_notified_at": now_ms - (10 * 60 * 1000),  # 10 minutes ago
    }
    monitor = {"keywords": ["ERROR"], "severity": "critical", "renotify_min": 60}
    project = {}
    action = evaluate_state(state, 2, monitor, project, GLOBAL_CONFIG)
    assert action == "SUPPRESS"


def test_evaluate_state_renotify():
    """ALARM + detection + renotify_min elapsed → RENOTIFY."""
    now_ms = int(time.time() * 1000)
    state = {
        "status": "ALARM",
        "last_notified_at": now_ms - (120 * 60 * 1000),  # 120 minutes ago
    }
    monitor = {"keywords": ["ERROR"], "severity": "critical", "renotify_min": 60}
    project = {}
    action = evaluate_state(state, 1, monitor, project, GLOBAL_CONFIG)
    assert action == "RENOTIFY"


def test_evaluate_state_suppress_disabled_renotify():
    """ALARM + detection + renotify_min=disabled → always SUPPRESS."""
    now_ms = int(time.time() * 1000)
    state = {
        "status": "ALARM",
        "last_notified_at": now_ms - (9999 * 60 * 1000),  # very long ago
    }
    monitor = {"keywords": ["ERROR"], "severity": "critical", "renotify_min": "disabled"}
    project = {}
    action = evaluate_state(state, 1, monitor, project, GLOBAL_CONFIG)
    assert action == "SUPPRESS"


def test_evaluate_state_recover():
    """ALARM + no detection + notify_on_recover=True → RECOVER."""
    state = {"status": "ALARM"}
    monitor = {"keywords": ["ERROR"], "severity": "critical"}
    project = {"notify_on_recover": True}
    action = evaluate_state(state, 0, monitor, project, GLOBAL_CONFIG)
    assert action == "RECOVER"


def test_evaluate_state_recover_silent():
    """ALARM + no detection + notify_on_recover=False → RECOVER_SILENT."""
    state = {"status": "ALARM"}
    monitor = {"keywords": ["ERROR"], "severity": "critical"}
    project = {"notify_on_recover": False}
    action = evaluate_state(state, 0, monitor, project, GLOBAL_CONFIG)
    assert action == "RECOVER_SILENT"


def test_evaluate_state_noop():
    """OK + no detection → NOOP."""
    state = {"status": "OK"}
    monitor = {"keywords": ["ERROR"], "severity": "critical"}
    project = {}
    action = evaluate_state(state, 0, monitor, project, GLOBAL_CONFIG)
    assert action == "NOOP"


def test_evaluate_state_noop_no_state():
    """No state + no detection → NOOP."""
    state = None
    monitor = {"keywords": ["ERROR"], "severity": "critical"}
    project = {}
    action = evaluate_state(state, 0, monitor, project, GLOBAL_CONFIG)
    assert action == "NOOP"
