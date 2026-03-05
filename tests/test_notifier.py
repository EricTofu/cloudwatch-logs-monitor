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

    def test_monitor_override(self):
        kw_config = {"severity": "critical"}
        monitor = {"sns_topic": "arn:monitor-specific"}
        topic = resolve_sns_topic(kw_config, monitor, GLOBAL_CONFIG)
        assert topic == "arn:monitor-specific"

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

    def test_normal_pagination(self):
        """Multiple log lines split across pages."""
        # Create lines that total > 3800 chars after header
        log_lines = [f"[{i}] 2026-01-01T00:00:00  Error message number {i} " + "x" * 100 for i in range(30)]
        context = "── [Context for Log 1] ──\nSome context line"
        pages = _split_log_lines_pages(log_lines, context, self.TEMPLATE, self.BASE_VARS)

        assert len(pages) >= 2
        # Page 1 should have context
        assert pages[0][1] == context
        # Page 2+ should say "(see page 1)"
        for _, ctx in pages[1:]:
            assert ctx == "(see page 1)"

    def test_page1_body_within_limit(self):
        """Page 1 rendered body must stay within max_desc (3800)."""
        log_lines = [f"[{i}] 2026-01-01T00:00:00  Error message {i} " + "x" * 100 for i in range(30)]
        context = "── [Context for Log 1] ──\n" + "Context line\n" * 50
        pages = _split_log_lines_pages(log_lines, context, self.TEMPLATE, self.BASE_VARS)

        # Render page 1 and check body length
        vars_p1 = {**self.BASE_VARS, "log_lines": pages[0][0], "context_lines": pages[0][1]}
        rendered_p1 = render_message(self.TEMPLATE, vars_p1)
        assert len(rendered_p1["body"]) <= 3800, (
            f"Page 1 body is {len(rendered_p1['body'])} chars, exceeds 3800"
        )

    def test_large_context_truncated_when_exceeds_budget(self):
        """When context alone exceeds the budget, it should be truncated."""
        log_lines = ["[1] 2026-01-01T00:00:00  Error message"]
        huge_context = "X" * 4000  # bigger than max_desc
        pages = _split_log_lines_pages(log_lines, huge_context, self.TEMPLATE, self.BASE_VARS)

        # Context on page 1 should be truncated
        assert "truncated" in pages[0][1] or "omitted" in pages[0][1]

        # Rendered body should still be within limit
        vars_p1 = {**self.BASE_VARS, "log_lines": pages[0][0], "context_lines": pages[0][1]}
        rendered_p1 = render_message(self.TEMPLATE, vars_p1)
        assert len(rendered_p1["body"]) <= 3800

    def test_single_page_when_fits(self):
        """When everything fits in one page, no pagination needed."""
        log_lines = ["[1] 2026-01-01T00:00:00  Short error"]
        context = "Brief context"
        pages = _split_log_lines_pages(log_lines, context, self.TEMPLATE, self.BASE_VARS)

        assert len(pages) == 1
        assert pages[0][1] == context

    def test_empty_log_lines(self):
        """Empty log lines should return a single page with '(no log lines)'."""
        pages = _split_log_lines_pages([], "some context", self.TEMPLATE, self.BASE_VARS)
        assert len(pages) == 1
        assert pages[0][0] == "(no log lines)"
        assert pages[0][1] == "some context"

    def test_small_available_first_preserves_context(self):
        """When available_first is small but >= 0, context should be fully preserved."""
        # Use moderate context that leaves only a small budget for log lines
        moderate_context = "C" * 2500
        log_lines = [f"[{i}] 2026-01-01T00:00:00  Error message number {i} " + "x" * 80 for i in range(20)]
        pages = _split_log_lines_pages(log_lines, moderate_context, self.TEMPLATE, self.BASE_VARS)

        # Page 1 context should be the full original context (not truncated)
        assert pages[0][1] == moderate_context
        # Should have multiple pages since page 1 has little room for log lines
        assert len(pages) >= 2


