"""Exclusion pattern filtering for log events."""

import logging
import re

logger = logging.getLogger(__name__)

# Characters that indicate a regex pattern (not a simple string)
_REGEX_META = re.compile(r'[\\.*+?^${}()|[\]]')


def is_simple_pattern(pattern):
    """Check if a pattern is a simple string (no regex metacharacters)."""
    return not _REGEX_META.search(pattern)


def compile_patterns(patterns):
    """Compile a list of patterns into regex objects.

    Invalid patterns are logged and skipped.
    Returns list of (original_pattern, compiled_regex) tuples.
    """
    compiled = []
    for pattern in patterns:
        try:
            compiled.append((pattern, re.compile(pattern)))
        except re.error as e:
            logger.warning("Invalid regex pattern '%s': %s (skipping)", pattern, e)
    return compiled


def apply_exclusions(events, project_patterns=None, monitor_patterns=None):
    """Filter events through PROJECT + MONITOR exclude patterns.

    Args:
        events: List of event dicts with "message" field.
        project_patterns: List of pattern strings from PROJECT.exclude_patterns.
        monitor_patterns: List of pattern strings from MONITOR.exclude_patterns.

    Returns:
        List of events that don't match any exclusion pattern.

    Note:
        Simple string patterns (no regex metacharacters) that were already
        applied at the Insights query level are skipped here to avoid
        double-filtering. Only regex patterns are applied at the app level.
    """
    project_patterns = project_patterns or []
    monitor_patterns = monitor_patterns or []

    # Only compile regex patterns (simple patterns were handled in the query)
    app_level_patterns = []
    for pattern in project_patterns + monitor_patterns:
        if not is_simple_pattern(pattern):
            try:
                app_level_patterns.append((pattern, re.compile(pattern)))
            except re.error as e:
                logger.warning("Invalid regex pattern '%s': %s (skipping)", pattern, e)

    if not app_level_patterns:
        return events

    filtered = []
    for event in events:
        message = event.get("message", "")
        excluded = False
        for pattern_str, pattern_re in app_level_patterns:
            if pattern_re.search(message):
                logger.debug("Excluded by pattern '%s': %s", pattern_str, message[:100])
                excluded = True
                break
        if not excluded:
            filtered.append(event)

    return filtered
