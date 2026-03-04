# CloudWatch Logs Monitor

DynamoDB 設定ベースの CloudWatch Logs 監視システム。Logs Insights クエリを DynamoDB に直書きし、Slack（AWS Chatbot）と Email へ通知します。

## 特徴

- **MONITOR 単位の設計** — 1 MONITOR = 1 クエリ + キーワード別 STATE 追跡
- **Insights クエリ直書き** — DynamoDB にクエリをそのまま保存、Lambda はそれを実行するだけ
- **キーワードグループ** — 同じ severity のキーワードをまとめて設定
- **EventBridge スケジューラ** — 5分/1時間/24時間など、スケジュールごとにモニター群を割り当て
- **固定検索ウィンドウ** — `last_searched_at` 不要、Lambda 失敗時も安全
- **状態管理とコンテンツベースの重複抑制** — ログの本文からUUIDや時刻をマスキングした**フィンガープリント**を生成してエラー内容ごとに ALARM / OK 状態を追跡。同じキーワードでも内容が異なるエラーは確実に通知します。
- **エラー分離** — 1 モニターの障害が他に影響しない
- **高度な Context 取得** — 該当ログの前後（双方向）のログを取得し、インメモリキャッシュで重複 API コールを抑制。

## アーキテクチャ

```
EventBridge Rules (スケジュール単位)
  ├── monitor-5min   rate(5 min)  → {"monitor_ids":["project-a","project-b"]}
  └── monitor-daily  rate(1 day)  → {"monitor_ids":["project-c-daily"]}
         ↓
Lambda → DynamoDB (MONITOR設定 + 生クエリ読み取り)
       → Logs Insights (クエリ実行)
       → SNS → Chatbot → Slack
       → SES → Email
```

## プロジェクト構成

```
├── src/log_monitor/
│   ├── handler.py         # Lambda エントリーポイント
│   ├── config.py          # DynamoDB CRUD
│   ├── query.py           # Insights クエリ実行 + 結果振り分け
│   ├── context.py         # 前後ログ行取得
│   ├── state.py           # 状態遷移ロジック
│   ├── notifier.py        # SNS + SES 通知
│   └── constants.py       # 定数
├── tests/
├── terraform/
├── scripts/
├── DESIGN.md
└── API_REFERENCE.md
```

## セットアップ

### 開発環境

```bash
uv sync --extra dev
uv run pytest tests/ -v
uv run ruff check src/ tests/
```

### デプロイ

```bash
cd terraform
terraform init
terraform plan -var="aws_profile=your-profile"
terraform apply -var="aws_profile=your-profile"
```

### スケジュール設定 (terraform.tfvars)

```hcl
schedules = {
  "5min" = {
    schedule_expression = "rate(5 minutes)"
    monitor_ids         = ["project-a", "project-b"]
  }
  "daily" = {
    schedule_expression = "rate(1 day)"
    monitor_ids         = ["project-c-daily"]
  }
}
```

### DynamoDB 初期データ

```bash
uv run python scripts/seed_dynamodb.py your-profile
```

## DynamoDB 設定例

### MONITOR レコード（キーワード付き）

```json
{
  "pk": "MONITOR", "sk": "project-a",
  "display_name": "Project Alpha",
  "log_group": "/aws/app/shared-logs",
  "search_window_minutes": 7,
  "query": "fields @timestamp, @message, @logStream\n| filter @logStream like /project-a/\n| filter @message like /ERROR|FATAL|TIMEOUT/\n| sort @timestamp asc\n| limit 500",
  "keywords": [
    { "words": ["ERROR", "FATAL"], "severity": "critical", "renotify_min": 30 },
    { "words": ["TIMEOUT"], "severity": "warning", "renotify_min": "disabled" }
  ],
  "notify_on_recover": true,
  "enabled": true
}
```

### MONITOR レコード（キーワードなし = レポート用途）

```json
{
  "pk": "MONITOR", "sk": "project-c-daily",
  "display_name": "Project Charlie Daily Report",
  "log_group": "/aws/app/project-c",
  "search_window_minutes": 1450,
  "query": "fields @timestamp, @message, @logStream\n| filter @message like /ERROR|WARN/\n| sort @timestamp asc\n| limit 1000",
  "severity": "info",
  "enabled": true
}
```

### GLOBAL#CONFIG（SES メール通知設定）

```json
{
  "pk": "GLOBAL", "sk": "CONFIG",
  "ses_config": {
    "from_address": "alerts@example.com",
    "reply_to": ["admin@example.com"],
    "recipients": {
      "critical": ["oncall@example.com", "manager@example.com"],
      "warning": ["team@example.com"]
    }
  }
}
```

### MONITOR レコード（SES 上書き）

```json
{
  "pk": "MONITOR", "sk": "project-a",
  "ses_config": {
    "from_address": "project-a@example.com",
    "recipients": ["lead@example.com", "dev-team@example.com"]
  }
}
```

> **解決順**: MONITOR `ses_config` → GLOBAL `ses_config`（severity 別）→ 未設定なら送信なし

## テスト

```bash
uv run pytest tests/ -v
uv run pytest tests/ -v --cov=src/log_monitor --cov-report=term-missing
```

## ライセンス

MIT
