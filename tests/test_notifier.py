"""Tests for notifier.py — SNS notification."""

import json

from log_monitor.notifier import (
    build_chatbot_payload,
    build_email_payload,
    render_message,
    resolve_email_sns_topic,
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
    "email_sns_topics": {
        "critical": "arn:aws:sns:ap-northeast-1:123:email-critical",
    },
    "notification_template": {
        "subject": "[{severity}] {project} - {keyword}",
        "body": "{keyword} detected {count} times",
    },
    "recover_template": {
        "subject": "[RECOVER] {project}",
        "body": "{keyword} recovered",
    },
}


class TestResolveSNSTopic:
    def test_monitor_override(self):
        monitor = {"severity": "critical", "override_sns_topic": "arn:monitor-specific"}
        topic = resolve_sns_topic(monitor, {}, GLOBAL_CONFIG)
        assert topic == "arn:monitor-specific"

    def test_project_override(self):
        monitor = {"severity": "critical"}
        project = {"override_sns_topics": {"critical": "arn:project-critical"}}
        topic = resolve_sns_topic(monitor, project, GLOBAL_CONFIG)
        assert topic == "arn:project-critical"

    def test_global_fallback(self):
        monitor = {"severity": "critical"}
        topic = resolve_sns_topic(monitor, {}, GLOBAL_CONFIG)
        assert topic == "arn:aws:sns:ap-northeast-1:123:slack-critical"


class TestResolveEmailSNSTopic:
    def test_project_override(self):
        project = {"override_email_sns_topics": {"critical": "arn:project-email"}}
        topics = resolve_email_sns_topic(project, GLOBAL_CONFIG)
        assert topics["critical"] == "arn:project-email"

    def test_global_fallback(self):
        topics = resolve_email_sns_topic({}, GLOBAL_CONFIG)
        assert topics["critical"] == "arn:aws:sns:ap-northeast-1:123:email-critical"


class TestResolveTemplate:
    def test_notification_template_global(self):
        monitor = {}
        project = {}
        tmpl = resolve_template(monitor, project, GLOBAL_CONFIG, "NOTIFY")
        assert tmpl["subject"] == "[{severity}] {project} - {keyword}"

    def test_recover_template(self):
        monitor = {}
        project = {}
        tmpl = resolve_template(monitor, project, GLOBAL_CONFIG, "RECOVER")
        assert tmpl["subject"] == "[RECOVER] {project}"

    def test_monitor_override(self):
        monitor = {"notification_template": {"subject": "custom", "body": "custom body"}}
        tmpl = resolve_template(monitor, {}, GLOBAL_CONFIG, "NOTIFY")
        assert tmpl["subject"] == "custom"


class TestRenderMessage:
    def test_basic_rendering(self):
        template = {"subject": "[{severity}] {project}", "body": "{keyword}: {count} events"}
        variables = {
            "severity": "CRITICAL",
            "project": "Alpha",
            "keyword": "ERROR",
            "count": "5",
        }
        result = render_message(template, variables)
        assert result["subject"] == "[CRITICAL] Alpha"
        assert result["body"] == "ERROR: 5 events"

    def test_missing_variable(self):
        template = {"subject": "{project} - {unknown}", "body": "test"}
        variables = {"project": "Alpha"}
        result = render_message(template, variables)
        assert result["subject"] == "Alpha - {unknown}"


class TestBuildPayloads:
    def test_chatbot_payload(self):
        payload = build_chatbot_payload("Title", "Description", "critical", ["ERROR"])
        parsed = json.loads(payload)
        assert parsed["version"] == "1.0"
        assert parsed["source"] == "custom"
        assert parsed["content"]["title"] == "Title"
        assert parsed["content"]["description"] == "Description"
        assert parsed["content"]["keywords"] == ["ERROR"]

    def test_email_payload(self):
        result = build_email_payload("Subject", "Body text")
        assert result == "Subject\n\nBody text"


class TestTruncateMessage:
    def test_no_truncation_needed(self):
        msg = "short message"
        assert truncate_message(msg) == msg

    def test_truncation(self):
        msg = "x" * (300 * 1024)  # 300KB
        result = truncate_message(msg)
        assert len(result.encode("utf-8")) <= 256 * 1024
        assert result.endswith("... (truncated)")
