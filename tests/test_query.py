"""Tests for query.py — Insights query builder and result dispatcher."""

from log_monitor.query import build_combined_query, dispatch_results


def test_build_combined_query_shared_log_group():
    project = {
        "sk": "project-a",
        "log_stream_pattern": "project-a",
        "exclude_patterns": ["healthcheck"],
        "monitors": [
            {"keywords": ["ERROR", "FATAL"], "severity": "critical"},
            {"keywords": ["TIMEOUT"], "severity": "warning"},
        ],
    }
    global_config = {"source_log_group": "/aws/app/shared-logs"}

    query = build_combined_query(project, global_config)

    assert "filter @logStream like /project-a/" in query
    assert "@message like /ERROR/" in query
    assert "@message like /FATAL/" in query
    assert "@message like /TIMEOUT/" in query
    assert "not like /healthcheck/" in query
    assert "sort @timestamp asc" in query


def test_build_combined_query_standalone_log_group():
    project = {
        "sk": "project-b",
        "override_log_group": "/aws/app/project-b",
        "monitors": [
            {"keywords": ["ERROR"], "severity": "critical"},
        ],
    }
    global_config = {"source_log_group": "/aws/app/shared-logs"}

    query = build_combined_query(project, global_config)

    # Should NOT have stream filter for standalone log group
    assert "@logStream like" not in query
    assert "@message like /ERROR/" in query


def test_build_combined_query_no_keywords():
    project = {"sk": "empty", "monitors": []}
    global_config = {}

    query = build_combined_query(project, global_config)
    assert query is None


def test_build_combined_query_regex_exclude_not_in_query():
    """Regex exclusion patterns should not be added to the Insights query."""
    project = {
        "sk": "project-a",
        "exclude_patterns": ["simple", "regex.*pattern"],
        "monitors": [{"keywords": ["ERROR"], "severity": "critical"}],
    }
    global_config = {"source_log_group": "/aws/app/shared-logs"}

    query = build_combined_query(project, global_config)

    # Simple pattern should be in query
    assert "not like /simple/" in query
    # Regex pattern should NOT be in query (handled app-side)
    assert "regex.*pattern" not in query


def test_dispatch_results():
    """Test dispatching Insights results to individual keywords."""
    results = [
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
    monitors = [
        {"keywords": ["ERROR", "FATAL"], "severity": "critical"},
        {"keywords": ["TIMEOUT"], "severity": "warning"},
    ]

    dispatched = dispatch_results(results, monitors)

    assert len(dispatched["ERROR"]) == 2  # "ERROR: db..." and "FATAL ERROR: oom"
    assert len(dispatched["FATAL"]) == 1  # "FATAL ERROR: oom"
    assert len(dispatched["TIMEOUT"]) == 1


def test_dispatch_results_empty():
    results = []
    monitors = [{"keywords": ["ERROR"], "severity": "critical"}]

    dispatched = dispatch_results(results, monitors)
    assert dispatched["ERROR"] == []
