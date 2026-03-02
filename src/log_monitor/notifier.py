"""SNS notification with Chatbot custom schema and Email support."""

import json
import logging
from datetime import datetime

from log_monitor.constants import JST, MAX_MESSAGE_BYTES, get_sns_client

logger = logging.getLogger(__name__)


def _resolve_severity(kw_config, monitor_config, global_config):
    """Resolve severity: keyword → monitor → global defaults."""
    return (
        kw_config.get("severity")
        or monitor_config.get("severity")
        or global_config.get("defaults", {}).get("severity", "warning")
    )


def resolve_sns_topic(kw_config, monitor_config, global_config):
    """Resolve Slack SNS topic: KEYWORD → MONITOR → GLOBAL.

    Uses severity to select the appropriate topic.
    """
    severity = _resolve_severity(kw_config, monitor_config, global_config)

    # 1. Keyword-level override
    if kw_config.get("sns_topic"):
        return kw_config["sns_topic"]

    # 2. MONITOR-level
    if monitor_config.get("sns_topic"):
        return monitor_config["sns_topic"]

    # 3. GLOBAL by severity
    return global_config.get("sns_topics", {}).get(severity)


def resolve_email_sns_topic(kw_config, monitor_config, global_config):
    """Resolve Email SNS topic: KEYWORD → MONITOR → GLOBAL."""
    severity = _resolve_severity(kw_config, monitor_config, global_config)

    # 1. MONITOR-level
    if monitor_config.get("email_sns_topic"):
        return monitor_config["email_sns_topic"]

    # 2. GLOBAL by severity
    return global_config.get("email_sns_topics", {}).get(severity)


def resolve_template(monitor_config, global_config, action):
    """Resolve notification template: MONITOR → GLOBAL."""
    template_key = "recover_template" if action == "RECOVER" else "notification_template"

    # 1. MONITOR-level
    tmpl = monitor_config.get(template_key)
    if tmpl:
        return tmpl

    # 2. GLOBAL default
    return global_config.get(template_key, {"subject": "{keyword}", "body": "{log_lines}"})


def render_message(template, variables):
    """Expand template variables like {project}, {keyword}, etc."""
    subject = template.get("subject", "")
    body = template.get("body", "")

    for key, value in variables.items():
        placeholder = "{" + key + "}"
        value_str = str(value) if value is not None else ""
        subject = subject.replace(placeholder, value_str)
        body = body.replace(placeholder, value_str)

    return {"subject": subject, "body": body}


def build_chatbot_payload(subject, body, severity, keywords_list=None):
    """Build AWS Chatbot custom notification schema JSON."""
    payload = {
        "version": "1.0",
        "source": "custom",
        "content": {
            "textType": "client-markdown",
            "title": subject,
            "description": body,
        },
    }

    if keywords_list:
        payload["content"]["keywords"] = keywords_list

    return json.dumps(payload, ensure_ascii=False)


def build_email_payload(subject, body):
    """Build plain text payload for email notifications."""
    return f"{subject}\n\n{body}"


def truncate_message(message, max_bytes=MAX_MESSAGE_BYTES):
    """Truncate message to fit within SNS size limit."""
    encoded = message.encode("utf-8")
    if len(encoded) <= max_bytes:
        return message

    truncation_marker = "\n\n... (truncated)"
    available = max_bytes - len(truncation_marker.encode("utf-8"))
    truncated = encoded[:available].decode("utf-8", errors="ignore")
    return truncated + truncation_marker


def send_notification(kw_config, monitor_config, global_config, action, events, keyword):
    """Orchestrator: resolve topics → render → publish to Slack + Email.

    Args:
        kw_config: Keyword group config dict.
        monitor_config: MONITOR config dict.
        global_config: GLOBAL config dict.
        action: State action ("NOTIFY", "RENOTIFY", "RECOVER").
        events: List of matching event dicts.
        keyword: The specific keyword that triggered this notification.
    """
    sns_client = get_sns_client()

    severity = _resolve_severity(kw_config, monitor_config, global_config)

    # Format log lines with timestamps
    log_line_parts = []
    for i, e in enumerate(events[:20], 1):
        ts = e.get("timestamp", "")
        msg = e.get("message", "").rstrip()
        log_line_parts.append(f"[{i}] {ts}  {msg}")
    log_lines = "\n".join(log_line_parts)

    # Context lines
    context_all = []
    for e in events[:5]:
        context_all.extend(e.get("context_lines", []))
    context_text = "\n".join(context_all) if context_all else "(no context)"

    # Stream names
    stream_names = list({e.get("log_stream", "") for e in events})

    variables = {
        "display_name": monitor_config.get("display_name", monitor_config.get("sk", "")),
        "keyword": keyword,
        "severity": severity.upper(),
        "count": str(len(events)),
        "detected_at": datetime.now(tz=JST).strftime("%Y-%m-%d %H:%M:%S JST"),
        "log_group": monitor_config.get("log_group", ""),
        "stream_name": ", ".join(stream_names[:3]),
        "log_lines": log_lines,
        "context_lines": context_text,
        "mention": monitor_config.get("mention", kw_config.get("mention", "")),
    }

    # Resolve and render template
    template = resolve_template(monitor_config, global_config, action)
    rendered = render_message(template, variables)

    # ── Slack notification (Chatbot) ──
    slack_topic = resolve_sns_topic(kw_config, monitor_config, global_config)
    if slack_topic:
        chatbot_json = build_chatbot_payload(
            rendered["subject"],
            rendered["body"],
            severity,
            keywords_list=[keyword, monitor_config.get("display_name", ""), severity],
        )
        chatbot_json = truncate_message(chatbot_json)
        try:
            sns_client.publish(
                TopicArn=slack_topic,
                Message=chatbot_json,
                Subject=rendered["subject"][:100],
            )
            logger.info("Slack notification sent: topic=%s, keyword=%s", slack_topic, keyword)
        except Exception:
            logger.exception("Failed to send Slack notification: topic=%s", slack_topic)

    # ── Email notification ──
    email_topic = resolve_email_sns_topic(kw_config, monitor_config, global_config)
    if email_topic:
        email_text = build_email_payload(rendered["subject"], rendered["body"])
        email_text = truncate_message(email_text)
        try:
            sns_client.publish(
                TopicArn=email_topic,
                Message=email_text,
                Subject=rendered["subject"][:100],
            )
            logger.info("Email notification sent: topic=%s, keyword=%s", email_topic, keyword)
        except Exception:
            logger.exception("Failed to send email notification: topic=%s", email_topic)
