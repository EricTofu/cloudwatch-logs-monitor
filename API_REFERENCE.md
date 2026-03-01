# API Reference — CloudWatch Logs Monitor

## `constants.py`

| Name | Type | Description |
|------|------|-------------|
| `TABLE_NAME` | `str` | DynamoDB table name (`"log-monitor"`) |
| `JST` | `timezone` | JST timezone (`UTC+9`) |
| `INGESTION_DELAY_MIN` | `int` | Logs ingestion delay buffer (2 min) |
| `POLL_INTERVAL_SEC` | `int` | Insights poll interval (1 sec) |
| `QUERY_TIMEOUT_SEC` | `int` | Insights max wait (120 sec) |
| `BATCH_SIZE` | `int` | Max concurrent Insights queries (25) |
| `MAX_MESSAGE_BYTES` | `int` | SNS max message size (256KB) |
| `get_logs_client()` | `→ botocore.client` | Cached boto3 `logs` client |
| `get_sns_client()` | `→ botocore.client` | Cached boto3 `sns` client |
| `get_dynamodb_resource()` | `→ boto3.resource` | Cached boto3 DynamoDB resource |

---

## `config.py`

| Function | Signature | Description |
|----------|-----------|-------------|
| `get_global_config` | `(table) → dict` | Fetch `GLOBAL#CONFIG` record |
| `query_all_projects` | `(table) → list[dict]` | Query all `pk=PROJECT` records (paginated) |
| `query_all_states` | `(table) → list[dict]` | Query all `pk=STATE` records (paginated) |
| `update_project_timestamp` | `(table, project_sk, timestamp_ms)` | Update `last_searched_at` on PROJECT |
| `update_state` | `(table, project_sk, keyword, action, count, now_ms)` | Create/update STATE record per action |
| `merge_defaults` | `(project, global_config) → dict` | Merge PROJECT fields with GLOBAL defaults |

---

## `query.py`

| Function | Signature | Description |
|----------|-----------|-------------|
| `build_combined_query` | `(project, global_config) → str` | Build Insights query with all keywords, stream pattern, simple exclusions |
| `start_all_queries` | `(active_projects, global_config, start_ms, end_ms) → dict` | Fire `start_query` for all projects. Returns `{query_id: project}` |
| `poll_all_queries` | `(pending: dict) → dict` | Poll `get_query_results` until all complete or timeout |
| `execute_all_queries` | `(active_projects, global_config, start_ms, end_ms) → dict` | Orchestrator: start → poll with batch splitting |
| `dispatch_results` | `(results: list, monitors: list) → dict[str, list]` | Distribute results to individual keywords. Returns `{keyword: [events]}` |

---

## `context.py`

| Function | Signature | Description |
|----------|-----------|-------------|
| `get_context_lines` | `(log_group, log_stream, timestamp_ms, num_lines) → list[str]` | Fetch N log lines before a timestamp via `GetLogEvents` |
| `enrich_with_context` | `(events, project, global_config) → list[dict]` | Add `context_lines` field to each event |

---

## `exclusion.py`

| Function | Signature | Description |
|----------|-----------|-------------|
| `apply_exclusions` | `(events, project_patterns, monitor_patterns) → list` | Filter events through PROJECT + MONITOR exclude patterns |
| `is_simple_pattern` | `(pattern: str) → bool` | Check if pattern has no regex metacharacters |
| `compile_patterns` | `(patterns: list) → list[re.Pattern]` | Compile regex patterns, log and skip invalid ones |

---

## `state.py`

| Function | Signature | Description |
|----------|-----------|-------------|
| `find_state` | `(states, project_sk, keyword) → dict \| None` | Lookup STATE for `project#keyword` |
| `evaluate_state` | `(state, count, monitor, project, global_config) → str` | Returns: `NOTIFY` / `RENOTIFY` / `SUPPRESS` / `RECOVER` / `RECOVER_SILENT` / `NOOP` |
| `resolve_renotify_min` | `(monitor, defaults) → int \| None` | Resolve with `"disabled"` / absent fallback |
| `resolve_notify_on_recover` | `(project, defaults) → bool` | Resolve with PROJECT → GLOBAL fallback |

---

## `notifier.py`

| Function | Signature | Description |
|----------|-----------|-------------|
| `resolve_sns_topic` | `(monitor, project, global_config) → str` | 3-tier fallback: MONITOR → PROJECT → GLOBAL |
| `resolve_email_sns_topic` | `(project, global_config) → str \| None` | 2-tier fallback: PROJECT → GLOBAL. `None` = skip email |
| `resolve_template` | `(monitor, project, global_config, action) → dict` | Resolve notification/recover template |
| `render_message` | `(template, variables) → dict` | Expand `{project}`, `{keyword}`, etc. |
| `build_chatbot_payload` | `(subject, body, severity, keywords_list) → str` | Build Chatbot custom schema JSON |
| `build_email_payload` | `(subject, body) → str` | Build plain text for email |
| `truncate_message` | `(message, max_bytes) → str` | Truncate to SNS 256KB limit |
| `send_notification` | `(monitor, project, global_config, action, events, keyword) → None` | Orchestrator: resolve → render → publish |

---

## `handler.py`

| Function | Signature | Description |
|----------|-----------|-------------|
| `handler` | `(event, context) → None` | Lambda entry point |
| `should_skip_project` | `(project, search_end_ms, defaults) → bool` | Check `schedule_rate_minutes` vs `last_searched_at` |
| `process_project` | `(project, query_results, states, global_config) → None` | Per-project: dispatch → exclude → state → notify → update |
