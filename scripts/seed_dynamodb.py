"""DynamoDB seed script — create GLOBAL and sample PROJECT records."""

import json
import sys

import boto3


def seed(profile_name=None, table_name="log-monitor", region="ap-northeast-1"):
    session = boto3.Session(profile_name=profile_name, region_name=region)
    dynamodb = session.resource("dynamodb")
    table = dynamodb.Table(table_name)

    # ── GLOBAL CONFIG ──
    global_config = {
        "pk": "GLOBAL",
        "sk": "CONFIG",
        "source_log_group": "/aws/app/shared-logs",
        "defaults": {
            "severity": "warning",
            "search_window_minutes": 5,
            "schedule_rate_minutes": 5,
            "renotify_min": 60,
            "notify_on_recover": True,
            "context_lines": 5,
        },
        "sns_topics": {
            "critical": "arn:aws:sns:ap-northeast-1:123456789012:slack-critical",
            "warning": "arn:aws:sns:ap-northeast-1:123456789012:slack-warning",
            "info": "arn:aws:sns:ap-northeast-1:123456789012:slack-info",
        },
        "email_sns_topics": {
            "critical": "arn:aws:sns:ap-northeast-1:123456789012:email-critical",
            "warning": "arn:aws:sns:ap-northeast-1:123456789012:email-alerts",
        },
        "notification_template": {
            "subject": "[{severity}] {project} - {keyword} 検出",
            "body": (
                "[{severity}] {project} {keyword}を検出\n"
                "{log_group}\n"
                "{stream_name}\n"
                "検出回数: {count}件\n"
                "検知内容：\n```\n{log_lines}\n```\n"
                "コンテキスト：\n```\n{context_lines}\n```"
            ),
        },
        "recover_template": {
            "subject": "[RECOVER] {project} - {keyword} 復旧",
            "body": "✅ *{project}* の *{keyword}* が復旧しました",
        },
    }

    # ── SAMPLE PROJECT (shared log group) ──
    project_a = {
        "pk": "PROJECT",
        "sk": "project-a",
        "display_name": "Project Alpha",
        "log_stream_pattern": "project-a",
        "enabled": True,
        "exclude_patterns": ["healthcheck", "ping OK"],
        "monitors": [
            {
                "keywords": ["ERROR", "FATAL"],
                "severity": "critical",
                "exclude_patterns": ["connection reset"],
                "renotify_min": 30,
                "context_lines": 10,
            },
            {
                "keywords": ["TIMEOUT"],
                "severity": "warning",
                "renotify_min": "disabled",
            },
        ],
    }

    # ── SAMPLE PROJECT (standalone log group) ──
    project_b = {
        "pk": "PROJECT",
        "sk": "project-b",
        "display_name": "Project Beta",
        "override_log_group": "/aws/app/project-b",
        "enabled": True,
        "monitors": [
            {
                "keywords": ["ERROR", "Exception"],
                "severity": "critical",
            },
            {
                "keywords": ["WARN"],
                "severity": "info",
            },
        ],
    }

    items = [global_config, project_a, project_b]
    for item in items:
        table.put_item(Item=item)
        print(f"✓ {item['pk']}#{item['sk']}")

    print(f"\nSeeded {len(items)} items to table '{table_name}'")


if __name__ == "__main__":
    profile = sys.argv[1] if len(sys.argv) > 1 else None
    seed(profile_name=profile)
