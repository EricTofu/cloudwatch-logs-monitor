"""Tests for notifier.py — SNS + SES notification."""

import json

from log_monitor.notifier import (
    build_chatbot_payload,
    build_email_payload,
    render_message,
    resolve_ses_from,
    resolve_ses_recipients,
    resolve_sns_topic,
    resolve_template,
    truncate_message,
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


class TestTruncateMessage:
    def test_no_truncation(self):
        assert truncate_message("short") == "short"

    def test_truncation(self):
        msg = "x" * (300 * 1024)
        result = truncate_message(msg)
        assert len(result.encode("utf-8")) <= 256 * 1024
        assert result.endswith("... (truncated)")
