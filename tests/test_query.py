"""Tests for query.py — execute + dispatch."""

from log_monitor.query import dispatch_results


def test_dispatch_results_with_keywords():
    raw_results = [
        [
            {"field": "@timestamp", "value": "2026-03-01 10:00:00.000"},
            {"field": "@message", "value": "ERROR: database connection failed"},
            {"field": "@logStream", "value": "project-a/stream-1"},
        ],
        [
            {"field": "@timestamp", "value": "2026-03-01 10:01:00.000"},
            {"field": "@message", "value": "TIMEOUT: request exceeded 30s"},
            {"field": "@logStream", "value": "project-a/stream-1"},
        ],
        [
            {"field": "@timestamp", "value": "2026-03-01 10:02:00.000"},
            {"field": "@message", "value": "FATAL ERROR: out of memory"},
            {"field": "@logStream", "value": "project-a/stream-2"},
        ],
    ]
    keywords_config = [
        {"words": ["ERROR", "FATAL"], "severity": "critical"},
        {"words": ["TIMEOUT"], "severity": "warning"},
    ]

    dispatched = dispatch_results(raw_results, keywords_config)

    assert len(dispatched["ERROR"]) == 2  # "ERROR: db..." and "FATAL ERROR: oom"
    assert len(dispatched["FATAL"]) == 1  # "FATAL ERROR: oom"
    assert len(dispatched["TIMEOUT"]) == 1


def test_dispatch_results_no_keywords():
    """When keywords_config is None, all results go to _all."""
    raw_results = [
        [
            {"field": "@timestamp", "value": "2026-03-01 10:00:00.000"},
            {"field": "@message", "value": "Some log message"},
            {"field": "@logStream", "value": "stream-1"},
        ],
    ]

    dispatched = dispatch_results(raw_results, None)
    assert len(dispatched["_all"]) == 1
    assert dispatched["_all"][0]["message"] == "Some log message"


def test_dispatch_results_empty():
    dispatched = dispatch_results([], [{"words": ["ERROR"], "severity": "critical"}])
    assert dispatched["ERROR"] == []


def test_dispatch_results_no_match():
    raw_results = [
        [
            {"field": "@timestamp", "value": "2026-03-01 10:00:00.000"},
            {"field": "@message", "value": "INFO: normal operation"},
            {"field": "@logStream", "value": "stream-1"},
        ],
    ]
    keywords_config = [{"words": ["ERROR"], "severity": "critical"}]

    dispatched = dispatch_results(raw_results, keywords_config)
    assert dispatched["ERROR"] == []
