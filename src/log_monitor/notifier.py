"""SNS notification with Chatbot custom schema and Email support."""

import json
import logging

from log_monitor.constants import JST, MAX_MESSAGE_BYTES, get_sns_client

logger = logging.getLogger(__name__)


def resolve_sns_topic(monitor, project, global_config):
    """Resolve Slack SNS topic with 3-tier fallback: MONITOR → PROJECT → GLOBAL.

    Uses the monitor's severity to select the appropriate topic.
    """
    severity = monitor.get("severity") or global_config.get("defaults", {}).get("severity", "warning")

    # 1. MONITOR-level override
    if monitor.get("override_sns_topic"):
        return monitor["override_sns_topic"]

    # 2. PROJECT-level override
    project_topics = project.get("override_sns_topics", {})
    if project_topics and severity in project_topics:
        return project_topics[severity]

    # 3. GLOBAL default
    return global_config.get("sns_topics", {}).get(severity)


def resolve_email_sns_topic(project, global_config):
    """Resolve Email SNS topic with 2-tier fallback: PROJECT → GLOBAL.

    Returns None if email notification should be skipped.
    """
    severity_topics = project.get("override_email_sns_topics")
    if severity_topics:
        return severity_topics  # project-level override (dict of severity→topic)

    return global_config.get("email_sns_topics")


def resolve_template(monitor, project, global_config, action):
    """Resolve notification template with 3-tier fallback.

    Uses recover_template for RECOVER actions.
    """
    template_key = "recover_template" if action == "RECOVER" else "notification_template"

    # 1. MONITOR-level
    tmpl = monitor.get(template_key)
    if tmpl:
        return tmpl

    # 2. PROJECT-level
    tmpl = project.get(template_key)
    if tmpl:
        return tmpl

    # 3. GLOBAL default
    return global_config.get(template_key, {"subject": "{keyword}", "body": "{log_lines}"})


def render_message(template, variables):
    """Expand template variables like {project}, {keyword}, etc.

    Args:
        template: Dict with "subject" and "body" keys.
        variables: Dict of variable name → value.

    Returns:
        Dict with "subject" and "body" expanded.
    """
    subject = template.get("subject", "")
    body = template.get("body", "")

    for key, value in variables.items():
        placeholder = "{" + key + "}"
        value_str = str(value) if value is not None else ""
        subject = subject.replace(placeholder, value_str)
        body = body.replace(placeholder, value_str)

    return {"subject": subject, "body": body}


def build_chatbot_payload(subject, body, severity, keywords_list=None):
    """Build AWS Chatbot custom notification schema JSON.

    Args:
        subject: Notification subject (used as title).
        body: Notification body (used as description).
        severity: Severity level (e.g., "critical").
        keywords_list: Optional list of keywords for the "keywords" field.

    Returns:
        JSON string in Chatbot custom notification format.
    """
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
    """Truncate message to fit within SNS size limit.

    Truncates at a UTF-8 byte boundary and appends a truncation marker.
    """
    encoded = message.encode("utf-8")
    if len(encoded) <= max_bytes:
        return message

    truncation_marker = "\n\n... (truncated)"
    available = max_bytes - len(truncation_marker.encode("utf-8"))

    # Truncate at UTF-8 byte boundary
    truncated = encoded[:available].decode("utf-8", errors="ignore")
    return truncated + truncation_marker


def send_notification(monitor, project, global_config, action, events, keyword):
    """Orchestrator: resolve topics → render → publish to Slack + Email.

    Args:
        monitor: Monitor config dict.
        project: Project config dict.
        global_config: GLOBAL config dict.
        action: State action ("NOTIFY", "RENOTIFY", "RECOVER").
        events: List of matching event dicts.
        keyword: The specific keyword that triggered this notification.
    """
    sns_client = get_sns_client()

    severity = monitor.get("severity") or global_config.get("defaults", {}).get("severity", "warning")

    # Build template variables
    from datetime import datetime

    # Format log lines: numbered with timestamps for readability
    log_line_parts = []
    for i, e in enumerate(events[:20], 1):  # max 20 events
        ts = e.get("timestamp", "")
        msg = e.get("message", "").rstrip()
        log_line_parts.append(f"[{i}] {ts}  {msg}")
    log_lines = "\n".join(log_line_parts)
    context_all = []
    for e in events[:5]:  # context for first 5 events
        context_all.extend(e.get("context_lines", []))
    context_text = "\n".join(context_all) if context_all else "(no context)"

    stream_names = list(set(e.get("log_stream", "") for e in events))

    variables = {
        "project": project.get("display_name", project.get("sk", "")),
        "keyword": keyword,
        "severity": severity.upper(),
        "count": str(len(events)),
        "detected_at": datetime.now(tz=JST).strftime("%Y-%m-%d %H:%M:%S JST"),
        "log_group": project.get("override_log_group") or global_config.get("source_log_group", ""),
        "stream_name": ", ".join(stream_names[:3]),
        "log_lines": log_lines,
        "context_lines": context_text,
        "streak": "",  # populated from state if needed
        "mention": monitor.get("mention", ""),
    }

    # Resolve and render template
    template = resolve_template(monitor, project, global_config, action)
    rendered = render_message(template, variables)

    # ── Slack notification (Chatbot) ──
    slack_topic = resolve_sns_topic(monitor, project, global_config)
    if slack_topic:
        chatbot_json = build_chatbot_payload(
            rendered["subject"],
            rendered["body"],
            severity,
            keywords_list=[keyword, project.get("display_name", ""), severity],
        )
        chatbot_json = truncate_message(chatbot_json)
        try:
            sns_client.publish(
                TopicArn=slack_topic,
                Message=chatbot_json,
                Subject=rendered["subject"][:100],  # SNS subject max 100 chars
            )
            logger.info("Slack notification sent: topic=%s, keyword=%s", slack_topic, keyword)
        except Exception:
            logger.exception("Failed to send Slack notification: topic=%s", slack_topic)

    # ── Email notification ──
    email_topics = resolve_email_sns_topic(project, global_config)
    if email_topics and isinstance(email_topics, dict):
        email_topic = email_topics.get(severity)
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
