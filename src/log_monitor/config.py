"""DynamoDB configuration reader and state updater."""

import logging
from decimal import Decimal

from boto3.dynamodb.conditions import Key

from log_monitor.constants import TABLE_NAME, get_dynamodb_resource

logger = logging.getLogger(__name__)


def _convert_decimals(obj):
    """Recursively convert Decimal values from DynamoDB to int/float."""
    if isinstance(obj, Decimal):
        return int(obj) if obj == int(obj) else float(obj)
    if isinstance(obj, dict):
        return {k: _convert_decimals(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_convert_decimals(i) for i in obj]
    return obj


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
    return _convert_decimals(item)


def get_monitor_config(monitor_id, table=None):
    """Fetch a single MONITOR record by its ID.

    Returns:
        Monitor config dict, or None if not found.
    """
    table = _get_table(table)
    resp = table.get_item(Key={"pk": "MONITOR", "sk": monitor_id})
    item = resp.get("Item")
    return _convert_decimals(item) if item else None


def get_state(monitor_id, keyword, fingerprint=None, table=None):
    """Fetch STATE record for a monitor#keyword#fingerprint combination."""
    table = _get_table(table)
    sk = f"{monitor_id}#{keyword}"
    if fingerprint:
        sk += f"#{fingerprint}"
    resp = table.get_item(Key={"pk": "STATE", "sk": sk})
    item = resp.get("Item")
    return _convert_decimals(item) if item else None


def get_active_alarm_fingerprints(monitor_id, keyword, table=None):
    """Fetch all active ALARM fingerprints for a monitor#keyword.
    
    Returns:
        List of fingerprints (or None if no fingerprint) that are currently in ALARM state.
    """
    table = _get_table(table)
    sk_prefix = f"{monitor_id}#{keyword}"

    resp = table.query(
        KeyConditionExpression=Key("pk").eq("STATE") & Key("sk").begins_with(sk_prefix)
    )

    active_fingerprints = []
    for item in resp.get("Items", []):
        if item.get("status") == "ALARM":
            sk = item["sk"]
            if sk == sk_prefix:
                active_fingerprints.append(None)
            elif sk.startswith(sk_prefix + "#"):
                fp = sk[len(sk_prefix) + 1:]
                active_fingerprints.append(fp)

    return active_fingerprints


def update_state(monitor_id, keyword, fingerprint, action, count, now_ms, original_message=None, table=None):
    """Create or update a STATE record based on the action."""
    table = _get_table(table)
    sk = f"{monitor_id}#{keyword}"
    if fingerprint:
        sk += f"#{fingerprint}"

    if action == "NOTIFY":
        if original_message is not None:
            table.update_item(
                Key={"pk": "STATE", "sk": sk},
                UpdateExpression=(
                    "SET #status = :alarm, "
                    "last_detected_at = :now, "
                    "last_notified_at = :now, "
                    "current_streak = :one, "
                    "detection_count = :count, "
                    "original_message = :original_msg"
                ),
                ExpressionAttributeNames={"#status": "status"},
                ExpressionAttributeValues={
                    ":alarm": "ALARM",
                    ":now": now_ms,
                    ":one": 1,
                    ":count": count,
                    ":original_msg": original_message,
                },
            )
        else:
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


def merge_defaults(config, global_config):
    """Merge MONITOR fields with GLOBAL defaults.

    Returns a new dict with resolved values, preferring MONITOR values
    over GLOBAL defaults.
    """
    defaults = global_config.get("defaults", {})
    return {
        "search_window_minutes": (
            config.get("search_window_minutes")
            or defaults.get("search_window_minutes", 7)
        ),
        "context_lines": (
            config.get("context_lines")
            if config.get("context_lines") is not None
            else defaults.get("context_lines", 5)
        ),
        "renotify_min": defaults.get("renotify_min", 60),
        "notify_on_recover": (
            config.get("notify_on_recover")
            if config.get("notify_on_recover") is not None
            else defaults.get("notify_on_recover", True)
        ),
        "severity": defaults.get("severity", "warning"),
    }
