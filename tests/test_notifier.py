"""Tests for notifier.py — SNS + SES notification."""

import json

from log_monitor.notifier import (
    _split_log_lines_pages,
    build_chatbot_payload,
    build_email_payload,
    render_message,
    resolve_ses_from,
    resolve_ses_recipients,
    resolve_sns_topic,
    resolve_template,
)

GLOBAL_CONFIG = {
    "defaults": {"severity": "warning"},
    "sns_topics": {
        "critical": "arn:aws:sns:ap-northeast-1:123:slack-critical",
        "warning": "arn:aws:sns:ap-northeast-1:123:slack-warning",
    },
    "ses_config": {
        "from_address": "alerts@example.com",
        "reply_to": ["admin@example.com"],
        "recipients": {
            "critical": ["oncall@example.com", "manager@example.com"],
            "warning": ["team@example.com"],
        },
    },
    "notification_template": {
        "subject": "[{severity}] {display_name} - {keyword}",
        "body": "{keyword} detected {count} times",
    },
    "recover_template": {
        "subject": "[RECOVER] {display_name}",
        "body": "{keyword} recovered",
    },
}


class TestResolveSNSTopic:
    def test_keyword_override(self):
        kw_config = {"severity": "critical", "sns_topic": "arn:kw-specific"}
        topic = resolve_sns_topic(kw_config, {}, GLOBAL_CONFIG)
        assert topic == "arn:kw-specific"

    def test_keyword_topics_map(self):
        kw_config = {"severity": "critical", "sns_topics": {"critical": "arn:kw-critical", "warning": "arn:kw-warning"}}
        topic = resolve_sns_topic(kw_config, {}, GLOBAL_CONFIG)
        assert topic == "arn:kw-critical"

    def test_keyword_override_beats_keyword_topics(self):
        kw_config = {"severity": "critical", "sns_topic": "arn:kw-force", "sns_topics": {"critical": "arn:kw-critical"}}
        topic = resolve_sns_topic(kw_config, {}, GLOBAL_CONFIG)
        assert topic == "arn:kw-force"

    def test_monitor_override(self):
        kw_config = {"severity": "critical"}
        monitor = {"sns_topic": "arn:monitor-specific"}
        topic = resolve_sns_topic(kw_config, monitor, GLOBAL_CONFIG)
        assert topic == "arn:monitor-specific"

    def test_monitor_topics_map(self):
        kw_config = {"severity": "warning"}
        monitor = {"sns_topics": {"critical": "arn:mon-critical", "warning": "arn:mon-warning"}}
        topic = resolve_sns_topic(kw_config, monitor, GLOBAL_CONFIG)
        assert topic == "arn:mon-warning"

    def test_monitor_override_beats_monitor_topics(self):
        kw_config = {"severity": "critical"}
        monitor = {"sns_topic": "arn:mon-force", "sns_topics": {"critical": "arn:mon-critical"}}
        topic = resolve_sns_topic(kw_config, monitor, GLOBAL_CONFIG)
        assert topic == "arn:mon-force"

    def test_keyword_beats_monitor(self):
        kw_config = {"severity": "critical", "sns_topics": {"critical": "arn:kw-critical"}}
        monitor = {"sns_topics": {"critical": "arn:mon-critical"}}
        topic = resolve_sns_topic(kw_config, monitor, GLOBAL_CONFIG)
        assert topic == "arn:kw-critical"

    def test_global_fallback(self):
        kw_config = {"severity": "critical"}
        topic = resolve_sns_topic(kw_config, {}, GLOBAL_CONFIG)
        assert topic == "arn:aws:sns:ap-northeast-1:123:slack-critical"


class TestResolveSESRecipients:
    def test_monitor_override(self):
        kw_config = {"severity": "critical"}
        monitor = {"ses_config": {"recipients": ["lead@example.com"]}}
        recipients = resolve_ses_recipients(kw_config, monitor, GLOBAL_CONFIG)
        assert recipients == ["lead@example.com"]

    def test_global_fallback_by_severity(self):
        kw_config = {"severity": "critical"}
        recipients = resolve_ses_recipients(kw_config, {}, GLOBAL_CONFIG)
        assert recipients == ["oncall@example.com", "manager@example.com"]

    def test_global_fallback_warning(self):
        kw_config = {"severity": "warning"}
        recipients = resolve_ses_recipients(kw_config, {}, GLOBAL_CONFIG)
        assert recipients == ["team@example.com"]

    def test_no_config(self):
        kw_config = {"severity": "info"}
        global_no_ses = {"defaults": {"severity": "info"}}
        recipients = resolve_ses_recipients(kw_config, {}, global_no_ses)
        assert recipients is None

    def test_case_insensitive_severity(self):
        kw_config = {"severity": "CRITICAL"}
        recipients = resolve_ses_recipients(kw_config, {}, GLOBAL_CONFIG)
        assert recipients == ["oncall@example.com", "manager@example.com"]


class TestResolveSESFrom:
    def test_monitor_override(self):
        monitor = {"ses_config": {"from_address": "project@example.com"}}
        from_addr = resolve_ses_from(monitor, GLOBAL_CONFIG)
        assert from_addr == "project@example.com"

    def test_global_fallback(self):
        from_addr = resolve_ses_from({}, GLOBAL_CONFIG)
        assert from_addr == "alerts@example.com"

    def test_no_config(self):
        from_addr = resolve_ses_from({}, {"defaults": {}})
        assert from_addr is None


class TestResolveTemplate:
    def test_notification_template(self):
        tmpl = resolve_template({}, GLOBAL_CONFIG, "NOTIFY")
        assert tmpl["subject"] == "[{severity}] {display_name} - {keyword}"

    def test_recover_template(self):
        tmpl = resolve_template({}, GLOBAL_CONFIG, "RECOVER")
        assert tmpl["subject"] == "[RECOVER] {display_name}"

    def test_monitor_override(self):
        monitor = {"notification_template": {"subject": "custom", "body": "custom body"}}
        tmpl = resolve_template(monitor, GLOBAL_CONFIG, "NOTIFY")
        assert tmpl["subject"] == "custom"


class TestRenderMessage:
    def test_basic(self):
        template = {"subject": "[{severity}] {display_name}", "body": "{keyword}: {count}"}
        variables = {"severity": "CRITICAL", "display_name": "Alpha", "keyword": "ERROR", "count": "5"}
        result = render_message(template, variables)
        assert result["subject"] == "[CRITICAL] Alpha"
        assert result["body"] == "ERROR: 5"

    def test_missing_variable(self):
        template = {"subject": "{display_name} - {unknown}", "body": "test"}
        result = render_message(template, {"display_name": "Alpha"})
        assert result["subject"] == "Alpha - {unknown}"


class TestBuildPayloads:
    def test_chatbot(self):
        payload = build_chatbot_payload("Title", "Description", "critical", ["ERROR"])
        parsed = json.loads(payload)
        assert parsed["version"] == "1.0"
        assert parsed["content"]["title"] == "Title"

    def test_email(self):
        result = build_email_payload("Subject", "Body")
        assert result == "Subject\n\nBody"


class TestSplitLogLinesPages:
    """Tests for _split_log_lines_pages pagination logic."""

    TEMPLATE = {"subject": "{keyword}", "body": "{log_lines}\n---\n{context_lines}"}
    BASE_VARS = {
        "display_name": "TestProject",
        "keyword": "ERROR",
        "fingerprint": "",
        "original_message": "",
        "severity": "WARNING",
        "count": "10",
        "detected_at": "2026-01-01 00:00:00 JST",
        "log_group": "/test/log-group",
        "stream_name": "stream-1",
        "mention": "",
    }

    def _make_entries(self, count, line_pad=100, with_context=True, ctx_limit=5):
        """Helper: build log_entries list of (log_line, context_str) tuples."""
        entries = []
        for i in range(1, count + 1):
            log_line = f"[{i}] 2026-01-01T00:00:00  Error message number {i} " + "x" * line_pad
            ctx = ""
            if with_context and i <= ctx_limit:
                ctx = f"── [Context for Log {i}] ──\nContext line for log {i}"
            entries.append((log_line, ctx))
        return entries

    def test_normal_pagination(self):
        """Multiple log entries split across pages."""
        entries = self._make_entries(30)
        pages = _split_log_lines_pages(entries, self.TEMPLATE, self.BASE_VARS)

        assert len(pages) >= 2
        # All pages should have their own content (no "see page 1")
        for log_lines_str, ctx_str in pages:
            assert "(see page 1)" not in ctx_str

    def test_log_and_context_stay_paired(self):
        """Each page's context corresponds to the log bodies on that page."""
        # Large context per log to force pagination
        entries = []
        for i in range(1, 11):
            log_line = f"[{i}] 2026-01-01T00:00:00  Error message number {i} " + "x" * 100
            ctx = ""
            if i <= 5:
                ctx = f"── [Context for Log {i}] ──\n" + f"Context line for log {i}\n" * 30
            entries.append((log_line, ctx))
        pages = _split_log_lines_pages(entries, self.TEMPLATE, self.BASE_VARS)

        assert len(pages) >= 2
        for log_lines_str, ctx_str in pages:
            # Extract log numbers from log lines on this page
            import re

            log_nums = re.findall(r"\[(\d+)\]", log_lines_str)
            # Each context header should match a log on this page
            ctx_nums = re.findall(r"Context for Log (\d+)", ctx_str)
            for cn in ctx_nums:
                assert cn in log_nums, f"Context for Log {cn} on page without its log body"

    def test_page_body_within_limit(self):
        """Each page rendered body must stay within max_desc (3800)."""
        entries = self._make_entries(30)
        pages = _split_log_lines_pages(entries, self.TEMPLATE, self.BASE_VARS)

        for page_log_lines, page_ctx in pages:
            vars_p = {**self.BASE_VARS, "log_lines": page_log_lines, "context_lines": page_ctx}
            rendered = render_message(self.TEMPLATE, vars_p)
            assert len(rendered["body"]) <= 3800, f"Page body is {len(rendered['body'])} chars, exceeds 3800"

    def test_large_context_truncated(self):
        """When a single entry's context exceeds the page budget, it should be truncated."""
        huge_ctx = "X" * 4000
        entries = [("[1] 2026-01-01T00:00:00  Error message", huge_ctx)]
        pages = _split_log_lines_pages(entries, self.TEMPLATE, self.BASE_VARS)

        # Context should be truncated or dropped
        assert "truncated" in pages[0][1] or pages[0][1] == ""

    def test_single_page_when_fits(self):
        """When everything fits in one page, no pagination needed."""
        entries = [("[1] 2026-01-01T00:00:00  Short error", "Brief context")]
        pages = _split_log_lines_pages(entries, self.TEMPLATE, self.BASE_VARS)

        assert len(pages) == 1
        assert "Brief context" in pages[0][1]

    def test_empty_log_entries(self):
        """Empty log entries should return a single page with '(no log lines)'."""
        pages = _split_log_lines_pages([], self.TEMPLATE, self.BASE_VARS)
        assert len(pages) == 1
        assert pages[0][0] == "(no log lines)"

    def test_no_context_entries(self):
        """Entries without context should paginate correctly with full budget for log lines."""
        entries = self._make_entries(20, line_pad=100, with_context=False)
        pages = _split_log_lines_pages(entries, self.TEMPLATE, self.BASE_VARS)

        for log_lines_str, ctx_str in pages:
            assert ctx_str == ""  # No context at all
            assert len(log_lines_str) > 0

    def test_mixed_context_entries(self):
        """Mix of entries with and without context; contexts stay paired."""
        entries = self._make_entries(10, line_pad=200, with_context=True, ctx_limit=3)
        pages = _split_log_lines_pages(entries, self.TEMPLATE, self.BASE_VARS)

        # Logs 4+ should never have context headers
        for log_lines_str, ctx_str in pages:
            import re

            log_nums = set(re.findall(r"\[(\d+)\]", log_lines_str))
            ctx_nums = set(re.findall(r"Context for Log (\d+)", ctx_str))
            # Only logs 1-3 can have context
            for cn in ctx_nums:
                assert int(cn) <= 3
                assert cn in log_nums
