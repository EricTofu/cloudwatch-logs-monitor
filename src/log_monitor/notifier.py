"""SNS notification with Chatbot custom schema and SES Email support."""

import json
import logging
from datetime import datetime

from log_monitor.constants import JST, get_ses_client, get_sns_client

logger = logging.getLogger(__name__)


def _resolve_severity(kw_config, monitor_config, global_config):
    """Resolve severity: keyword → monitor → global defaults."""
    return (
        kw_config.get("severity")
        or monitor_config.get("severity")
        or global_config.get("defaults", {}).get("severity", "warning")
    )


def _get_topic_by_severity(topics_dict, severity):
    """Case-insensitive lookup in sns_topics dict."""
    if not topics_dict or not severity:
        return None
    # Exact match first
    if severity in topics_dict:
        return topics_dict[severity]
    # Case-insensitive fallback
    severity_lower = severity.lower()
    for key, value in topics_dict.items():
        if key.lower() == severity_lower:
            return value
    return None


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

    # 3. GLOBAL by severity (case-insensitive)
    return _get_topic_by_severity(global_config.get("sns_topics", {}), severity)


def resolve_ses_recipients(kw_config, monitor_config, global_config):
    """Resolve SES recipients: MONITOR → GLOBAL (by severity)."""
    # 1. MONITOR-level
    ses_config = monitor_config.get("ses_config", {})
    if ses_config.get("recipients"):
        recipients = ses_config["recipients"]
        if isinstance(recipients, list):
            return recipients

    # 2. GLOBAL by severity
    severity = _resolve_severity(kw_config, monitor_config, global_config)
    global_ses = global_config.get("ses_config", {})
    recipients_map = global_ses.get("recipients", {})
    if recipients_map and severity:
        # Case-insensitive lookup
        if severity in recipients_map:
            return recipients_map[severity]
        severity_lower = severity.lower()
        for key, value in recipients_map.items():
            if key.lower() == severity_lower:
                return value

    return None


def resolve_ses_from(monitor_config, global_config):
    """Resolve SES from address: MONITOR → GLOBAL."""
    # 1. MONITOR-level
    ses_config = monitor_config.get("ses_config", {})
    if ses_config.get("from_address"):
        return ses_config["from_address"]

    # 2. GLOBAL
    global_ses = global_config.get("ses_config", {})
    return global_ses.get("from_address")


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
    for i, e in enumerate(events[:50], 1):
        ts = e.get("timestamp", "")
        msg = e.get("message", "").rstrip()
        log_line_parts.append(f"[{i}] {ts}  {msg}")

    # Context lines
    context_all = []
    for e in events[:5]:
        context_all.extend(e.get("context_lines", []))
    context_text = "\n".join(context_all) if context_all else "(no context)"

    # Stream names
    stream_names = list({e.get("log_stream", "") for e in events})

    # Base variables (shared across pages)
    base_variables = {
        "display_name": monitor_config.get("display_name", monitor_config.get("sk", "")),
        "keyword": keyword,
        "severity": severity.upper(),
        "count": str(len(events)),
        "detected_at": datetime.now(tz=JST).strftime("%Y-%m-%d %H:%M:%S JST"),
        "log_group": monitor_config.get("log_group", ""),
        "stream_name": "\n".join(stream_names[:3]),
        "mention": monitor_config.get("mention", kw_config.get("mention", "")),
    }

    template = resolve_template(monitor_config, global_config, action)

    # ── Slack notification (Chatbot) — paginate log lines ──
    slack_topic = resolve_sns_topic(kw_config, monitor_config, global_config)
    if slack_topic:
        # Split log_lines into pages that fit within Chatbot's 4096-char limit
        log_pages = _split_log_lines_pages(log_line_parts, context_text, template, base_variables)
        total_pages = len(log_pages)

        for page_num, (page_log_lines, page_context) in enumerate(log_pages, 1):
            variables = {**base_variables, "log_lines": page_log_lines, "context_lines": page_context}
            rendered = render_message(template, variables)

            title = rendered["subject"]
            if total_pages > 1:
                title = f"{title} ({page_num}/{total_pages})"

            chatbot_json = build_chatbot_payload(
                title,
                rendered["body"],
                severity,
                keywords_list=[keyword, monitor_config.get("display_name", ""), severity],
            )
            try:
                sns_client.publish(
                    TopicArn=slack_topic,
                    Message=chatbot_json,
                    Subject=title[:100],
                )
                logger.info(
                    "Slack notification sent: topic=%s, keyword=%s, page=%d/%d",
                    slack_topic, keyword, page_num, total_pages,
                )
            except Exception:
                logger.exception("Failed to send Slack notification: topic=%s", slack_topic)
    else:
        logger.warning(
            "No Slack SNS topic found: severity=%s, keyword=%s", severity, keyword
        )

    # ── Email notification (SES) ──
    ses_recipients = resolve_ses_recipients(kw_config, monitor_config, global_config)
    ses_from = resolve_ses_from(monitor_config, global_config)
    if ses_recipients and ses_from:
        variables = {
            **base_variables,
            "log_lines": "\n\n".join(log_line_parts),
            "context_lines": context_text,
        }
        rendered = render_message(template, variables)
        email_body = build_email_payload(rendered["subject"], rendered["body"])

        # reply_to from MONITOR → GLOBAL
        mon_ses = monitor_config.get("ses_config", {})
        reply_to = mon_ses.get("reply_to") or global_config.get("ses_config", {}).get("reply_to", [])

        try:
            ses_client = get_ses_client()
            ses_client.send_email(
                Source=ses_from,
                Destination={"ToAddresses": ses_recipients},
                ReplyToAddresses=reply_to,
                Message={
                    "Subject": {"Data": rendered["subject"][:998], "Charset": "UTF-8"},
                    "Body": {"Text": {"Data": email_body, "Charset": "UTF-8"}},
                },
            )
            logger.info(
                "SES email sent: from=%s, to=%s, keyword=%s",
                ses_from, ses_recipients, keyword,
            )
        except Exception:
            logger.exception("Failed to send SES email: from=%s, to=%s", ses_from, ses_recipients)
    else:
        logger.warning(
            "No SES email config found: recipients=%s, from=%s, keyword=%s",
            ses_recipients, ses_from, keyword,
        )


def _split_log_lines_pages(log_line_parts, context_text, template, base_variables):
    """Split log lines into pages that fit within Chatbot's description limit.

    Returns list of (log_lines_str, context_str) tuples.
    Page 1 includes context, subsequent pages have log lines only.
    """
    max_desc = 3800  # Chatbot limit is 4096, leave room for JSON wrapper

    # Estimate header size (template with empty log_lines/context)
    test_vars = {**base_variables, "log_lines": "", "context_lines": ""}
    test_rendered = render_message(template, test_vars)
    header_size = len(test_rendered["body"])

    # Page 1 needs room for context text, other pages don't
    context_cost = len(context_text) if context_text else 0
    available_first = max_desc - header_size - context_cost
    available_rest = max_desc - header_size
    if available_first < 200:
        available_first = 2000  # fallback
    if available_rest < 200:
        available_rest = 2000  # fallback

    pages = []
    current_lines = []
    current_len = 0

    for line in log_line_parts:
        line_len = len(line) + 1
        available = available_first if not pages else available_rest
        if current_len + line_len > available and current_lines:
            ctx = context_text if not pages else "(see page 1)"
            pages.append(("\n".join(current_lines), ctx))
            current_lines = [line]
            current_len = line_len
        else:
            current_lines.append(line)
            current_len += line_len

    if current_lines:
        ctx = context_text if not pages else "(see page 1)"
        pages.append(("\n".join(current_lines), ctx))

    if not pages:
        pages.append(("(no log lines)", context_text))

    return pages

