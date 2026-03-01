"""Tests for exclusion.py — pattern filtering."""

from log_monitor.exclusion import apply_exclusions, compile_patterns, is_simple_pattern


def test_is_simple_pattern():
    assert is_simple_pattern("healthcheck") is True
    assert is_simple_pattern("ping OK") is True
    assert is_simple_pattern("error.*timeout") is False
    assert is_simple_pattern("[0-9]+") is False
    assert is_simple_pattern("connection reset") is True


def test_compile_patterns_valid():
    patterns = ["error.*timeout", "fatal"]
    compiled = compile_patterns(patterns)
    assert len(compiled) == 2


def test_compile_patterns_invalid():
    patterns = ["[invalid", "valid"]
    compiled = compile_patterns(patterns)
    assert len(compiled) == 1  # invalid pattern skipped
    assert compiled[0][0] == "valid"


def test_apply_exclusions_regex_patterns():
    events = [
        {"message": "ERROR: database connection failed"},
        {"message": "ERROR: connection reset by peer"},
        {"message": "ERROR: out of memory"},
        {"message": "ERROR during healthcheck handler"},
    ]

    # Only regex patterns are applied app-side (simple ones are in query)
    project_patterns = ["healthcheck"]  # simple → skipped (already in query)
    monitor_patterns = ["connection.*reset"]  # regex → applied here

    result = apply_exclusions(events, project_patterns, monitor_patterns)
    messages = [e["message"] for e in result]

    # "connection reset" should be excluded by regex
    assert "ERROR: connection reset by peer" not in messages
    # "healthcheck" is simple, so NOT excluded here (already done in query)
    assert "ERROR during healthcheck handler" in messages
    assert len(result) == 3


def test_apply_exclusions_empty_patterns():
    events = [
        {"message": "ERROR: something"},
        {"message": "WARNING: something else"},
    ]
    result = apply_exclusions(events, [], [])
    assert len(result) == 2


def test_apply_exclusions_all_excluded():
    events = [
        {"message": "ERROR: connection reset"},
    ]
    result = apply_exclusions(events, [], [r"connection\s+reset"])
    assert len(result) == 0
