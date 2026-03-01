"""DynamoDB configuration reader and state updater."""

import logging

from log_monitor.constants import TABLE_NAME, get_dynamodb_resource

logger = logging.getLogger(__name__)


def _get_table(table=None):
    if table is not None:
        return table
    return get_dynamodb_resource().Table(TABLE_NAME)


def get_global_config(table=None):
    """Fetch the GLOBAL#CONFIG record."""
    table = _get_table(table)
    resp = table.get_item(Key={"pk": "GLOBAL", "sk": "CONFIG"})
    item = resp.get("Item")
    if not item:
        raise ValueError("GLOBAL#CONFIG record not found in DynamoDB")
    return item


def query_all_projects(table=None):
    """Query all pk=PROJECT records with pagination."""
    table = _get_table(table)
    items = []
    kwargs = {
        "KeyConditionExpression": "pk = :pk",
        "ExpressionAttributeValues": {":pk": "PROJECT"},
    }
    while True:
        resp = table.query(**kwargs)
        items.extend(resp.get("Items", []))
        if "LastEvaluatedKey" not in resp:
            break
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
    return items


def query_all_states(table=None):
    """Query all pk=STATE records with pagination."""
    table = _get_table(table)
    items = []
    kwargs = {
        "KeyConditionExpression": "pk = :pk",
        "ExpressionAttributeValues": {":pk": "STATE"},
    }
    while True:
        resp = table.query(**kwargs)
        items.extend(resp.get("Items", []))
        if "LastEvaluatedKey" not in resp:
            break
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
    return items


def update_project_timestamp(project_sk, timestamp_ms, table=None):
    """Update last_searched_at on a PROJECT record."""
    table = _get_table(table)
    table.update_item(
        Key={"pk": "PROJECT", "sk": project_sk},
        UpdateExpression="SET last_searched_at = :ts",
        ExpressionAttributeValues={":ts": timestamp_ms},
    )


def update_state(project_sk, keyword, action, count, now_ms, table=None):
    """Create or update a STATE record based on the action."""
    table = _get_table(table)
    sk = f"{project_sk}#{keyword}"

    if action == "NOTIFY":
        table.update_item(
            Key={"pk": "STATE", "sk": sk},
            UpdateExpression=(
                "SET #status = :alarm, "
                "last_detected_at = :now, "
                "last_notified_at = :now, "
                "current_streak = :one, "
                "detection_count = :count"
            ),
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={
                ":alarm": "ALARM",
                ":now": now_ms,
                ":one": 1,
                ":count": count,
            },
        )
    elif action == "RENOTIFY":
        table.update_item(
            Key={"pk": "STATE", "sk": sk},
            UpdateExpression=(
                "SET last_detected_at = :now, "
                "last_notified_at = :now, "
                "current_streak = current_streak + :one, "
                "detection_count = detection_count + :count"
            ),
            ExpressionAttributeValues={
                ":now": now_ms,
                ":one": 1,
                ":count": count,
            },
        )
    elif action == "SUPPRESS":
        table.update_item(
            Key={"pk": "STATE", "sk": sk},
            UpdateExpression=(
                "SET last_detected_at = :now, "
                "current_streak = current_streak + :one, "
                "detection_count = detection_count + :count"
            ),
            ExpressionAttributeValues={
                ":now": now_ms,
                ":one": 1,
                ":count": count,
            },
        )
    elif action in ("RECOVER", "RECOVER_SILENT"):
        table.update_item(
            Key={"pk": "STATE", "sk": sk},
            UpdateExpression=(
                "SET #status = :ok, "
                "last_detected_at = :null, "
                "last_notified_at = :null, "
                "current_streak = :zero, "
                "detection_count = :zero"
            ),
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={
                ":ok": "OK",
                ":null": None,
                ":zero": 0,
            },
        )
    # NOOP: do nothing


def merge_defaults(project, global_config):
    """Merge PROJECT fields with GLOBAL defaults for convenience.

    Returns a dict with resolved values for schedule_rate_minutes,
    search_window_minutes, notify_on_recover, and context_lines.
    """
    defaults = global_config.get("defaults", {})
    return {
        "schedule_rate_minutes": (
            project.get("schedule_rate_minutes")
            or defaults.get("schedule_rate_minutes", 5)
        ),
        "search_window_minutes": (
            project.get("search_window_minutes")
            or defaults.get("search_window_minutes", 5)
        ),
        "notify_on_recover": (
            project.get("notify_on_recover")
            if project.get("notify_on_recover") is not None
            else defaults.get("notify_on_recover", True)
        ),
        "context_lines": defaults.get("context_lines", 5),
    }
