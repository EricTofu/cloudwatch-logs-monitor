# API Reference

## handler.py

| Function | Signature | Description |
|----------|-----------|-------------|
| `handler` | `(event, context)` | Lambda entry point. Processes `event["monitor_ids"]` |
| `process_monitor` | `(monitor_id, global_config, search_end_ms, now_ms)` | Single monitor: query → dispatch → evaluate → notify |

## config.py

| Function | Signature | Description |
|----------|-----------|-------------|
| `get_global_config` | `(table=None)` | Fetch `GLOBAL#CONFIG` record |
| `get_monitor_config` | `(monitor_id, table=None)` | Fetch `MONITOR` record by ID |
| `get_state` | `(monitor_id, keyword, table=None)` | Fetch `STATE` record for `monitor#keyword` |
| `update_state` | `(monitor_id, keyword, action, count, now_ms, table=None)` | Update STATE (NOTIFY/RENOTIFY/SUPPRESS/RECOVER) |
| `merge_defaults` | `(config, global_config)` | Merge MONITOR with GLOBAL defaults |

## query.py

| Function | Signature | Description |
|----------|-----------|-------------|
| `execute_query` | `(log_group, query_string, search_start_ms, search_end_ms)` | Run Insights query (start + poll) |
| `dispatch_results` | `(raw_results, keywords_config)` | Dispatch results to keywords or `_all` |

## context.py

| Function | Signature | Description |
|----------|-----------|-------------|
| `get_context_lines` | `(log_group, log_stream, timestamp_ms, num_lines=5)` | Fetch N log lines before event |
| `enrich_with_context` | `(events, monitor_config, global_config)` | Add `context_lines` to events |

## state.py

| Function | Signature | Description |
|----------|-----------|-------------|
| `evaluate_state` | `(state, count, kw_config, monitor_config, global_config)` | Return action: NOTIFY/RENOTIFY/SUPPRESS/RECOVER/NOOP |
| `resolve_renotify_min` | `(kw_config, defaults)` | Resolve renotify_min with fallback |
| `resolve_notify_on_recover` | `(config, defaults)` | Resolve notify_on_recover with fallback |

## notifier.py

| Function | Signature | Description |
|----------|-----------|-------------|
| `send_notification` | `(kw_config, monitor_config, global_config, action, events, keyword)` | Publish to Slack (Chatbot) + SES Email |
| `resolve_sns_topic` | `(kw_config, monitor_config, global_config)` | 3-tier Slack topic resolution |
| `resolve_ses_recipients` | `(kw_config, monitor_config, global_config)` | SES recipients: MONITOR → GLOBAL (severity) |
| `resolve_ses_from` | `(monitor_config, global_config)` | SES from address: MONITOR → GLOBAL |
| `resolve_template` | `(monitor_config, global_config, action)` | Template resolution |
| `render_message` | `(template, variables)` | Expand `{variable}` placeholders |
| `build_chatbot_payload` | `(subject, body, severity, keywords_list=None)` | AWS Chatbot JSON schema |
| `build_email_payload` | `(subject, body)` | Plain text for email |

## constants.py

| Constant / Function | Value | Description |
|----------|-------|-------------|
| `TABLE_NAME` | `"cloudwatch-logs-monitor"` | DynamoDB table name |
| `JST` | `UTC+9` | Japan Standard Time |
| `INGESTION_DELAY_MIN` | `2` | CloudWatch log ingestion delay buffer |
| `POLL_INTERVAL_SEC` | `1` | Insights poll interval |
| `QUERY_TIMEOUT_SEC` | `120` | Insights query timeout |
| `MAX_MESSAGE_BYTES` | `256KB` | SNS message size limit |
| `get_logs_client()` | — | Cached CloudWatch Logs client |
| `get_sns_client()` | — | Cached SNS client |
| `get_ses_client()` | — | Cached SES client |
| `get_dynamodb_resource()` | — | Cached DynamoDB resource |
