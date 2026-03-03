#!/usr/bin/env python3
"""Seed DynamoDB with GLOBAL config and sample MONITOR records."""

import sys

import boto3


def main():
    if len(sys.argv) < 2:
        print("Usage: python seed_dynamodb.py <aws_profile>")
        sys.exit(1)

    profile = sys.argv[1]
    session = boto3.Session(profile_name=profile)
    dynamodb = session.resource("dynamodb", region_name="ap-northeast-1")
    table = dynamodb.Table("cloudwatch-logs-monitor")

    # ── GLOBAL CONFIG ──
    table.put_item(Item={
        "pk": "GLOBAL",
        "sk": "CONFIG",
        "defaults": {
            "severity": "warning",
            "search_window_minutes": 7,
            "context_lines": 5,
            "renotify_min": 60,
            "notify_on_recover": True,
        },
        "sns_topics": {
            "critical": "arn:aws:sns:ap-northeast-1:ACCOUNT_ID:slack-critical",
            "warning": "arn:aws:sns:ap-northeast-1:ACCOUNT_ID:slack-warning",
            "info": "arn:aws:sns:ap-northeast-1:ACCOUNT_ID:slack-info",
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
            "subject": "[{severity}] {display_name} - {keyword} 検出",
            "body": (
                "{mention}\n"
                "検出回数: {count}件\n"
                "検出時刻: {detected_at}\n"
                "ログループ: {log_group}\n"
                "ストリーム: {stream_name}\n\n"
                "── ログ行 ──\n{log_lines}\n\n"
                "── コンテキスト ──\n{context_lines}"
            ),
        },
        "recover_template": {
            "subject": "[RECOVER] {display_name} - {keyword} 復旧",
            "body": "✅ {display_name} {keyword} が復旧しました",
        },
    })
    print("✅ GLOBAL#CONFIG created")

    # ── SAMPLE MONITOR: 5分間隔 ──
    table.put_item(Item={
        "pk": "MONITOR",
        "sk": "project-a",
        "display_name": "Project Alpha",
        "log_group": "/aws/app/shared-logs",
        "search_window_minutes": 7,
        "context_lines": 10,
        "query": (
            "fields @timestamp, @message, @logStream\n"
            "| filter @logStream like /project-a/\n"
            "| filter (@message like /ERROR/ or @message like /FATAL/ or @message like /TIMEOUT/)\n"
            "| filter @message not like /healthcheck/\n"
            "| sort @timestamp asc\n"
            "| limit 500"
        ),
        "keywords": [
            {
                "words": ["ERROR", "FATAL"],
                "severity": "critical",
                "renotify_min": 30,
                "mention": "@channel",
            },
            {
                "words": ["TIMEOUT"],
                "severity": "warning",
                "renotify_min": "disabled",
            },
        ],
        "notify_on_recover": True,
        "enabled": True,
    })
    print("✅ MONITOR: project-a created")

    # ── SAMPLE MONITOR: 24時間間隔（キーワードなし = レポート用途） ──
    table.put_item(Item={
        "pk": "MONITOR",
        "sk": "project-c-daily",
        "display_name": "Project Charlie Daily Report",
        "log_group": "/aws/app/project-c",
        "search_window_minutes": 1450,
        "query": (
            "fields @timestamp, @message, @logStream\n"
            "| filter @message like /ERROR|WARN|TIMEOUT/\n"
            "| sort @timestamp asc\n"
            "| limit 1000"
        ),
        "severity": "info",
        "notify_on_recover": False,
        "enabled": True,
    })
    print("✅ MONITOR: project-c-daily created (no keywords, monitor-level)")

    print("\n🎉 Seed complete!")


if __name__ == "__main__":
    main()
