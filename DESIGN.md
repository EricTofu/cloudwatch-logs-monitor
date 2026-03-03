# CloudWatch Logs Monitor — 設計ドキュメント

> 作成日: 2026-03-01

## 1. 背景・課題

- AWS上の1つのアカウントに**複数プロジェクト**（約10個）が存在
- **大半のプロジェクト**は**同じロググループ**にログを出力し、ログストリーム名にプロジェクト識別子が含まれる
- **一部のプロジェクト**は**単独のロググループ**を所有し、全ログストリームが検索対象
- ログ本文にはプロジェクト識別子が含まれない
- 各プロジェクトごとに**特定のキーワードを監視**し、SNS → Chatbot → Slack、および SES → Email に通知したい
- プロジェクトごとにキーワード、緊急度、除外リスト、通知先、検索期間、通知間隔などカスタマイズしたい
- 一部ログは **Infrequent Access クラス**に格納されているため、`FilterLogEvents` は使用不可
- 将来的にはロググループをプロジェクトごとに分離する予定

## 2. 要件

| カテゴリ | 要件 |
|---------|------|
| 監視 | 複数プロジェクトのキーワード検知を1つの Lambda で実現 |
| 検索 | **CloudWatch Logs Insights** のクエリで検索（Infrequent Access 対応） |
| 通知先 | GLOBAL / プロジェクト / キーワード 単位でカスタマイズ可能 |
| 通知チャネル | **Slack**: SNS → Chatbot → Slack（Chatbot カスタムスキーマ対応） |
| | **Email**: SES → Email（DynamoDB 設定で宛先管理） |
| 通知内容 | テンプレートベースで GLOBAL / プロジェクト / キーワード 単位でカスタマイズ可能 |
| 通知頻度 | 重複抑制（silence period）+ 再通知間隔。間隔はカスタマイズ可能 |
| 緊急度 | CRITICAL / WARNING / ERROR / INFO。通知先・通知方法に連動 |
| 除外リスト | キーワードに一致しても特定パターンを含むログは通知対象外にできる |
| コンテキスト | 検出ログの直前ログも含めて送信（行数は設定可能） |
| 復旧通知 | ALARM → OK 遷移時に通知。プロジェクト単位で有効/無効を設定可能 |
| 設定管理 | DynamoDB でリアルタイムに設定変更可能 |
| 可視化 | 初期実装では省略。将来 CloudWatch メトリクスを追加可能な構造 |
| デプロイ | Terraform |
| 将来対応 | ロググループ分離後もコード変更なしで動作 |

## 3. アーキテクチャ

### 3.1 全体構成図

```
                    ┌─────────────────────┐
                    │  DynamoDB            │
                    │  ┌───────────────┐   │
                    │  │ GLOBAL CONFIG │   │
                    │  │ PROJECT (×N)  │   │
                    │  │ STATE (自動)   │   │
                    │  └───────────────┘   │
                    └──────────┬──────────┘
                         読み取り / 書き込み
                               │
  EventBridge ──────────► Lambda (monitor)
  (5分間隔)                    │
                               ├──── CloudWatch Logs Insights
                               │     ┌──────────────────────────────────┐
                               │     │ 共有ロググループ                    │
                               │     │ /aws/app/shared-logs              │
                               │     │   ├── project-a/stream-1          │
                               │     │   ├── project-b/stream-1          │
                               │     │   └── ...                         │
                               │     │                                    │
                               │     │ 単独ロググループ                    │
                               │     │ /aws/app/project-c                │
                               │     │   ├── stream-1                    │
                               │     │   ├── stream-2                    │
                               │     │   └── ...                         │
                               │     └──────────────────────────────────┘
                               │
                               ├──── GetLogEvents（コンテキスト取得）
                               │
                               └──── SNS Publish (Slack通知) + SES SendEmail
                                         │
                               ┌─────────┼─────────────────────┐
                               │         │                     │
                           SNS Topic  SNS Topic             SES
                         (Slack用)   (Slack用)          (Email用)
                          critical    warning/info     ses_config
                               │         │                     │
                           Chatbot    Chatbot           Email
                                │         │         Recipients
                          Slack      Slack
```

### 3.2 ロググループのパターン

プロジェクトは2つのパターンに分かれる：

| パターン | ロググループ | ストリーム名 | Insights クエリ |
|---------|------------|-------------|----------------|
| **共有型** | `/aws/app/shared-logs` | `project-a/xxx` | `filter @logStream like /project-a/` |
| **単独型** | `/aws/app/project-c` | `stream-1`, `stream-2` | ストリームフィルタ不要（全ストリーム検索） |

設定による切り替え:
- **共有型**: `override_log_group` = null, `log_stream_pattern` = "project-a"
- **単独型**: `override_log_group` = "/aws/app/project-c", `log_stream_pattern` = null

### 3.3 データフロー

```
┌──────────────────────────────────────────────────────────────────────┐
│ Lambda 実行フロー（5分ごと）                                          │
│                                                                      │
│  ① DynamoDB から GLOBAL + 全 PROJECT + 全 STATE を一括取得            │
│          │                                                           │
│          ▼                                                           │
│  ② プロジェクトごとにスケジュール判定                                   │
│     schedule_rate_minutes 未到達 → スキップ                            │
│          │                                                           │
│          ▼                                                           │
│  ③ Logs Insights クエリ実行                                           │
│     ┌──────────────────────────────────────────┐                     │
│     │ for each project:                        │                     │
│     │   start_query() → 非同期開始              │                     │
│     │                                          │                     │
│     │ 全プロジェクトの start_query 完了後、      │                     │
│     │ バッチで get_query_results をポーリング    │                     │
│     └──────────────────────────────────────────┘                     │
│          │                                                           │
│          ▼                                                           │
│  ④ 除外パターンでフィルタリング                                        │
│     ┌──────────────────────────────────────────┐                     │
│     │ クエリ内除外: Insights クエリの filter 句  │                     │
│     │ アプリ側除外: 正規表現パターン              │                     │
│     └──────────────────────────────────────────┘                     │
│          │                                                           │
│          ▼                                                           │
│  ⑤ コンテキスト取得（設定されている場合）                                │
│     GetLogEvents で検出ログ直前の N 行を取得                            │
│          │                                                           │
│          ▼                                                           │
│  ⑥ 状態遷移の判定（DynamoDB STATE を参照）                              │
│     ┌───────────────────────────────────────────────────────┐        │
│     │ 検出あり & status=OK      → NOTIFY（初回通知）          │        │
│     │ 検出あり & status=ALARM   → 再通知間隔チェック           │        │
│     │ 検出なし & status=ALARM   → RECOVER（復旧）            │        │
│     │ 検出なし & status=OK      → NOOP                      │        │
│     └───────────────────────────────────────────────────────┘        │
│          │                                                           │
│          ▼                                                           │
│  ⑦ 通知送信（該当する場合のみ）                                        │
│     ┌─────────────────────────────────────────────┐                  │
│     │ Slack通知: Chatbot カスタムスキーマ JSON       │                  │
│     │ Email通知: プレーンテキスト（別 Topic）        │                  │
│     │ 通知先: MONITOR → PROJECT → GLOBAL の順で解決  │                  │
│     └─────────────────────────────────────────────┘                  │
│          │                                                           │
│          ▼                                                           │
│  ⑧ DynamoDB STATE 更新 & last_searched_at 更新                       │
└──────────────────────────────────────────────────────────────────────┘
```

### 3.4 将来の移行パス

```
【現在: 共有ロググループ + 一部単独】      【将来: 全プロジェクト分離後】

 /aws/app/shared-logs                    /aws/app/project-a ←─┐
      │                                  /aws/app/project-b    │
  Lambda が log_stream_pattern で              /aws/app/project-c    │
  プロジェクトを識別して検索                                      │
                                          DynamoDB の設定変更のみ │
                                          override_log_group ────┘
                                          log_stream_pattern → null

                                          Lambda コードは変更なし！
```

## 4. DynamoDB テーブル設計

### テーブル名: `cloudwatch-logs-monitor`

**キー構成**: `pk` (Partition Key) + `sk` (Sort Key)

```
pk              sk                  管理者          説明
──────────────  ──────────────────  ──────────────  ──────────────────
GLOBAL          CONFIG              人間が編集       グローバル設定
PROJECT         project-a           人間が編集       プロジェクト設定
PROJECT         project-b           人間が編集       プロジェクト設定
STATE           project-a#ERROR     Lambda が自動    状態（人間は触らない）
STATE           project-a#TIMEOUT   Lambda が自動    状態
```

### 4.1 GLOBAL#CONFIG（グローバル設定 — 1レコード）

```json
{
  "pk": "GLOBAL",
  "sk": "CONFIG",

  "source_log_group": "/aws/app/shared-logs",

  "defaults": {
    "severity": "warning",
    "search_window_minutes": 5,
    "schedule_rate_minutes": 5,
    "renotify_min": 60,
    "notify_on_recover": true,
    "context_lines": 5
  },

  "sns_topics": {
    "critical": "arn:aws:sns:ap-northeast-1:123456789:slack-critical",
    "warning":  "arn:aws:sns:ap-northeast-1:123456789:slack-warning",
    "info":     "arn:aws:sns:ap-northeast-1:123456789:slack-info"
  },

  "ses_config": {
    "from_address": "alerts@example.com",
    "reply_to": ["admin@example.com"],
    "recipients": {
      "critical": ["oncall@example.com", "manager@example.com"],
      "warning": ["team@example.com"],
      "info": null
    }
  },

  "notification_template": {
    "subject": "[{severity}] {project} - {keyword} 検出",
    "body": "[{severity}] {project} {keyword}を検出\n{log_group}\n{stream_name}\n検出回数: {count}件\n検知内容：\n```\n{log_lines}\n```\nコンテキスト：\n```\n{context_lines}\n```"
  },

  "recover_template": {
    "subject": "[RECOVER] {project} - {keyword} 復旧",
    "body": "✅ *{project}* の *{keyword}* が復旧しました"
  }
}
```

### 4.2 PROJECT レコード（1プロジェクト1レコード）

#### 共有ロググループ型

```json
{
  "pk": "PROJECT",
  "sk": "project-a",

  "display_name": "Project Alpha",
  "log_stream_pattern": "project-a",
  "override_log_group": null,
  "enabled": true,

  "schedule_rate_minutes": null,
  "search_window_minutes": null,
  "notify_on_recover": true,

  "exclude_patterns": ["healthcheck", "ping OK"],

  "override_sns_topics": {
    "critical": "arn:aws:sns:...:project-a-critical"
  },
  "ses_config": null,

  "notification_template": null,
  "recover_template": null,

  "monitors": [
    {
      "keywords": ["ERROR", "FATAL", "Exception"],
      "severity": "critical",
      "exclude_patterns": ["connection reset", "cache miss"],
      "renotify_min": 30,
      "context_lines": 10,
      "mention": "@channel",
      "override_sns_topic": null,
      "notification_template": null
    },
    {
      "keywords": ["TIMEOUT"],
      "severity": "warning",
      "renotify_min": "disabled"
    },
    {
      "keywords": ["OOM"],
      "severity": "critical",
      "override_sns_topic": "arn:aws:sns:...:team-b-slack",
      "notification_template": {
        "subject": "[OOM] Project Alpha - 緊急",
        "body": "💀 *OOM 発生！*\n即時対応が必要です\n---\n{log_lines}"
      }
    }
  ]
}
```

#### 単独ロググループ型

```json
{
  "pk": "PROJECT",
  "sk": "project-c",

  "display_name": "Project Charlie",
  "log_stream_pattern": null,
  "override_log_group": "/aws/app/project-c",
  "enabled": true,

  "monitors": [
    { "keywords": ["ERROR", "FATAL"], "severity": "critical" },
    { "keywords": ["WARN"],  "severity": "info" }
  ]
}
```

**最小構成のルール**: 指定しないフィールドはすべて GLOBAL defaults にフォールバック。

> **Note**: `keywords` はリスト形式で、同一緊急度のキーワードをまとめて設定できる。Lambda は内部で個々のキーワードに展開し、STATE は **キーワード単位** で独立して管理する（例: ERROR が復旧しても FATAL が検出中なら FATAL は ALARM のまま）。

> **Note**: `last_searched_at` は Lambda が自動管理するフィールド。人間が設定する必要はない。初回実行時は `defaults.search_window_minutes` を使用。

### 4.3 STATE レコード（Lambda 自動管理）

```json
{
  "pk": "STATE",
  "sk": "project-a#ERROR",
  "status": "ALARM",
  "last_detected_at": 1740000000000,
  "last_notified_at": 1740000000000,
  "detection_count": 42,
  "current_streak": 3
}
```

> **Note**: タイムスタンプは内部処理を **epoch ミリ秒** で統一。表示時のみ JST 変換する。

STATE は Lambda が初回検出時に自動作成。RECOVER 時に `last_detected_at` と `last_notified_at` をクリアし、次回の ALARM で stale なタイムスタンプが使われるのを防ぐ。

## 5. Logs Insights クエリ設計

### 5.1 基本クエリ構造

#### 共有ロググループ（log_stream_pattern あり）

```
fields @timestamp, @message, @logStream
| filter @logStream like /project-a/
| filter @message like /ERROR/
| sort @timestamp asc
| limit 100
```

#### 単独ロググループ（log_stream_pattern なし）

```
fields @timestamp, @message, @logStream
| filter @message like /ERROR/
| sort @timestamp asc
| limit 100
```

### 5.2 除外パターンの適用

除外パターンはクエリ内とアプリ側の2段階で適用：

**段階1: クエリ内除外**（シンプルな文字列パターンのみ）
```
| filter @message not like /healthcheck/
| filter @message not like /ping OK/
```

**段階2: アプリ側除外**（正規表現パターン）
- クエリ内で処理できない複雑な正規表現はアプリ側で `re.search()` を使用
- 除外パターンに正規表現メタ文字が含まれる場合はアプリ側にフォールバック

### 5.3 非同期実行戦略

キーワード結合（§5.4）により、プロジェクト単位で1クエリにまとめる。10プロジェクトなら **10クエリ** で済む。

Logs Insights は非同期API（`start_query` → `get_query_results`）だが、**サーバー側で並列実行**されるため、Python の `asyncio` やマルチスレッドは不要。`start_query` を一斉に発行し、ポーリングで完了を待つだけで効率的に動作する。

```python
POLL_INTERVAL_SEC = 1
QUERY_TIMEOUT_SEC = 120  # 最大待機時間

def execute_all_queries(active_projects, global_config, search_start_ms, search_end_ms):
    logs_client = boto3.client("logs")

    # ──────────────────────────────────────────
    # Phase 1: start_query の一斉発行（~1-2秒）
    #   → サーバー側で全クエリが同時に実行開始
    # ──────────────────────────────────────────
    pending = {}
    for project in active_projects:
        log_group = project.get("override_log_group") or global_config["source_log_group"]
        query_string = build_combined_query(project, global_config)

        query_id = logs_client.start_query(
            logGroupName=log_group,
            startTime=search_start_ms // 1000,  # epoch秒
            endTime=search_end_ms // 1000,
            queryString=query_string
        )["queryId"]

        pending[query_id] = project

    # ──────────────────────────────────────────
    # Phase 2: 全クエリの完了をポーリング（~5-30秒）
    # ──────────────────────────────────────────
    completed = {}
    start_time = time.time()

    while pending:
        if time.time() - start_time > QUERY_TIMEOUT_SEC:
            for qid, proj in pending.items():
                logger.error("Query timeout: project=%s, query_id=%s", proj["sk"], qid)
            break

        time.sleep(POLL_INTERVAL_SEC)

        for query_id in list(pending.keys()):
            resp = logs_client.get_query_results(queryId=query_id)
            status = resp["status"]

            if status == "Complete":
                completed[pending[query_id]["sk"]] = {
                    "project": pending[query_id],
                    "results": resp["results"]
                }
                del pending[query_id]
            elif status in ("Failed", "Cancelled", "Timeout"):
                logger.error("Query %s for project %s: %s",
                             query_id, pending[query_id]["sk"], status)
                del pending[query_id]
            # "Running", "Scheduled" → 次のポーリングで再チェック

    return completed
```

#### 所要時間の比較

| 方式 | 10クエリの所要時間 |
|------|-------------------|
| **順次実行** (start → wait → start → wait...) | 10 × 5-10秒 = **50-100秒** |
| **一斉発行 + ポーリング** (↑の方式) | max(5-10秒) + ポーリング = **10-30秒** |

#### タイムアウト対策

- `QUERY_TIMEOUT_SEC = 120` を超えたらタイムアウト扱い。該当プロジェクトはスキップされるが、`last_searched_at` は更新されないため**次回再検索**される
- Lambda 自体のタイムアウト（10分）内に十分収まる

#### API 同時実行制限

CloudWatch Logs Insights は **アカウントあたり同時 30 クエリ** の制限がある。現状10プロジェクトなら問題ないが、将来30超の場合はバッチ分割する：

```python
BATCH_SIZE = 25  # 30制限に対してマージン確保

def execute_in_batches(projects, ...):
    all_results = {}
    for i in range(0, len(projects), BATCH_SIZE):
        batch = projects[i:i + BATCH_SIZE]
        results = execute_all_queries(batch, ...)
        all_results.update(results)
    return all_results
```

### 5.4 プロジェクト単位のキーワード結合

同一プロジェクトの全 monitors の `keywords` をフラットに展開し、1つの Insights クエリにまとめる。

```
fields @timestamp, @message, @logStream
| filter @logStream like /project-a/
| filter (
    @message like /ERROR/
    or @message like /FATAL/
    or @message like /Exception/
    or @message like /TIMEOUT/
    or @message like /OOM/
  )
| sort @timestamp asc
| limit 500
```

結果はアプリ側でキーワードごとに振り分け、各キーワードの STATE を独立して更新する。

**メリット**:
- 10プロジェクト × 1クエリ = **10クエリ** で済む（同時30制限内）
- 同じデータを複数回スキャンしないため**コスト削減**

**デメリット**:
- アプリ側でのキーワード振り分けロジックが必要
- `limit` をキーワード数に応じて増やす必要がある

## 6. カスタマイズ詳細

### 6.1 通知先の解決（3段階フォールバック）

```
Slack通知先:
  1. MONITOR の override_sns_topic     ← キーワード固有
  2. PROJECT の override_sns_topics    ← プロジェクト固有
  3. GLOBAL の sns_topics              ← デフォルト

Email通知先:
  1. MONITOR の ses_config.recipients     ← モニター固有
  2. GLOBAL の ses_config.recipients      ← severity ベースで解決
  3. 未設定 → Email通知しない
```

| ケース | Slack通知先 | Email通知先 |
|--------|-----------|------------|
| project-a / ERROR (critical) | project-a-critical | GLOBAL ses_config critical |
| project-a / OOM (critical) | team-b-slack | GLOBAL ses_config critical |
| project-b / ERROR (critical) | GLOBAL slack-critical | GLOBAL ses_config critical |
| project-c / WARN (info) | GLOBAL slack-info | 送信しない (未設定) |

### 6.2 通知内容の解決（3段階フォールバック）

```
  1. MONITOR の notification_template  ← キーワード固有テンプレート
  2. PROJECT の notification_template  ← プロジェクト固有テンプレート
  3. GLOBAL の notification_template   ← デフォルトテンプレート
```

**テンプレート変数**:

| 変数 | 内容 |
|------|------|
| `{project}` | プロジェクト表示名 |
| `{keyword}` | 検出キーワード |
| `{severity}` | 緊急度 |
| `{count}` | 今回の検出数 |
| `{detected_at}` | 検出時刻（JST） |
| `{log_group}` | ロググループ名 |
| `{stream_name}` | 検出されたストリーム名 |
| `{log_lines}` | 検出ログ（最大 N 行） |
| `{context_lines}` | 検出ログ直前の N 行 |
| `{streak}` | 連続検出回数 |
| `{mention}` | メンション（設定されている場合） |

### 6.3 通知頻度（silence period）

| パラメータ | スコープ | 説明 |
|-----------|---------|------|
| `renotify_min` | MONITOR → GLOBAL defaults | ALARM 継続時の再通知間隔 |
| | | 数値 → その分数後に再通知 |
| | | `"disabled"` → 再通知なし |
| | | 未設定 → GLOBAL defaults にフォールバック |

> **Note**: `null` ではなく `"disabled"` を使用し、DynamoDB での null vs 未設定の曖昧さを回避。

### 6.4 復旧通知

| パラメータ | スコープ | 説明 |
|-----------|---------|------|
| `notify_on_recover` | PROJECT → GLOBAL defaults | 復旧時に通知するか |

### 6.5 プロジェクト単位のスケジュール

| パラメータ | スコープ | デフォルト | 説明 |
|-----------|---------|-----------|------|
| `schedule_rate_minutes` | PROJECT / GLOBAL defaults | 5 | 検索実行間隔 |
| `search_window_minutes` | PROJECT / GLOBAL defaults | 5 | 検索対象の時間幅 |

**ユースケース: 1日1回の全量検索**
```json
{
  "schedule_rate_minutes": 1440,
  "search_window_minutes": 1440
}
```

## 7. 通知フォーマット

### 7.1 Chatbot カスタムスキーマ（Slack向け）

SNS → Chatbot → Slack では、Chatbot が解釈できる **カスタム通知スキーマ** で送信する必要がある。

```json
{
  "version": "1.0",
  "source": "custom",
  "content": {
    "textType": "client-markdown",
    "title": "[CRITICAL] Project Alpha - ERROR 検出",
    "description": "[CRITICAL] Project Alpha ERRORを検出\n/aws/app/shared-logs\nproject-a/stream-1\n検出回数: 3件\n検知内容：\n```\n2026-03-01 ERROR: database connection failed\n```\nコンテキスト：\n```\n2026-03-01 INFO: processing request\n2026-03-01 INFO: connecting to database\n```",
    "nextSteps": [
      "CloudWatch Logs を確認: https://console.aws.amazon.com/...",
      "対象サーバーの状態を確認"
    ],
    "keywords": ["ERROR", "Project Alpha", "CRITICAL"]
  }
}
```

### 7.2 Email 通知（SES プレーンテキスト）

Email は SES `SendEmail` API を使用し、**プレーンテキスト形式** で送信。テンプレート変数を展開したそのままの文字列。宛先は DynamoDB の `ses_config.recipients` から解決。

### 7.3 重複検出の処理

同じキーワードで複数ログが検出された場合：
- 検出回数 (`count`) を記録
- 通知には**最初の N 件**のログ内容を含める（`max_log_lines` で設定）
- 重複メッセージ（完全一致）は除去し、ユニークなログのみ表示

## 8. 状態遷移ロジック

### 8.1 状態遷移図

```
                    ┌─────────────────────────────────────────────┐
                    │                                             │
                    │     ┌──────────────────────────────────┐    │
                    ▼     ▼                                  │    │
            ┌─────────────────┐     検出あり              ┌──┴────┴──┐
  START ──► │       OK        │ ──────NOTIFY──────────►   │  ALARM   │
            │                 │                           │          │
            │                 │ ◄──── RECOVER ──────────  │          │
            │                 │  (notify_on_recover=true) │          │
            │                 │                           │          │
            │                 │ ◄── RECOVER_SILENT ─────  │          │
            │                 │  (notify_on_recover=false) │          │
            └─────────────────┘                           └──────────┘
               検出なし → NOOP                    │              ▲
                                                  │              │
                                        SUPPRESS (間隔内)  RENOTIFY (間隔超過)
                                                  │              │
                                                  └──────────────┘
```

### 8.2 ロジック（擬似コード）

```python
def evaluate_state(state, matches, monitor, global_config):
    count = len(matches)
    status = state.get("status", "OK") if state else "OK"
    defaults = global_config["defaults"]

    # renotify_min の解決: "disabled" → None, 未設定 → defaults
    renotify = monitor.get("renotify_min", "FALLBACK")
    if renotify == "FALLBACK":
        renotify = defaults.get("renotify_min")
    elif renotify == "disabled":
        renotify = None

    # notify_on_recover の解決: PROJECT → GLOBAL
    notify_on_recover = project.get("notify_on_recover")
    if notify_on_recover is None:
        notify_on_recover = defaults.get("notify_on_recover", False)

    if count > 0:
        if status == "OK":
            return "NOTIFY"
        elif status == "ALARM":
            last = state.get("last_notified_at")
            if last and renotify and minutes_since(last) >= renotify:
                return "RENOTIFY"
            return "SUPPRESS"
    else:
        if status == "ALARM":
            return "RECOVER" if notify_on_recover else "RECOVER_SILENT"
        return "NOOP"
```

### 8.3 STATE 更新ルール

| アクション | status 更新 | last_detected_at | last_notified_at | current_streak | detection_count |
|-----------|-----------|-----------------|-----------------|---------------|----------------|
| NOTIFY | ALARM | now | now | 1 | count |
| RENOTIFY | ALARM | now | now | +1 | +count |
| SUPPRESS | ALARM | now | 変更なし | +1 | +count |
| RECOVER | OK | クリア | クリア | 0 | 0 |
| RECOVER_SILENT | OK | クリア | クリア | 0 | 0 |
| NOOP | 変更なし | - | - | - | - |

> **重要**: RECOVER 時に `last_detected_at` と `last_notified_at` をクリアすることで、次回 ALARM 時に stale なタイムスタンプが使われるのを防ぐ。

## 9. Lambda 処理ロジック

### 9.1 メイン処理フロー

```python
def handler(event, context):
    # 1. 設定を一括取得
    global_config = get_global_config()
    projects = query_all_projects()
    states = query_all_states()

    search_end_ms = current_epoch_ms() - (2 * 60 * 1000)  # 2分バッファ

    # 2. スケジュール判定 & クエリ発行（並列）
    pending = {}
    for project in projects:
        if not project.get("enabled", True):
            continue
        if should_skip_project(project, search_end_ms):
            continue

        query_id = start_insights_query(project, global_config, search_end_ms)
        pending[query_id] = project

    # 3. 結果のバッチポーリング
    results = poll_all_queries(pending)

    # 4. プロジェクトごとに処理
    for project, query_results in results:
        try:
            process_project(project, query_results, states, global_config)
        except Exception:
            logger.exception("Failed to process project %s", project["sk"])
            continue

        # last_searched_at 更新
        update_project_timestamp(project["sk"], search_end_ms)
```

### 9.2 エラー分離

プロジェクト間のエラー分離を必ず実装。1プロジェクトの失敗が他プロジェクトに影響しない。

### 9.3 API制限対策

| 対策 | 説明 |
|------|------|
| **Insights 同時実行数** | アカウント上限30。バッチ分割で対応 |
| **boto3 リトライ** | Standard Retry Mode 有効化 |
| **取り込み遅延バッファ** | 検索終了時刻を `now - 2分` に設定 |
| **タイムアウト安全設計** | `last_searched_at` はプロジェクト単位で更新 |
| **SNS サイズ制限** | メッセージを最大 256KB に truncate |

## 10. プロジェクト構成

```
cloudwatch-logs-monitor/
├── src/
│   └── log_monitor/
│       ├── __init__.py
│       ├── handler.py          # Lambda エントリポイント
│       ├── config.py           # DynamoDB 設定読み込み
│       ├── query.py            # Logs Insights クエリ構築＆実行
│       ├── context.py          # GetLogEvents でコンテキスト取得
│       ├── exclusion.py        # 除外パターンフィルタリング
│       ├── state.py            # 状態遷移ロジック
│       ├── notifier.py         # SNS通知（Chatbotスキーマ）+ SES Email
│       └── constants.py        # 共有定数（JST, テーブル名等）
├── tests/
│   ├── conftest.py
│   ├── test_config.py
│   ├── test_query.py
│   ├── test_context.py
│   ├── test_exclusion.py
│   ├── test_state.py
│   ├── test_notifier.py
│   └── test_handler.py
├── terraform/
│   ├── main.tf
│   ├── variables.tf
│   └── outputs.tf
├── scripts/
│   └── seed_dynamodb.py
├── DESIGN.md
├── REQUEST.md
├── README.md
├── pyproject.toml
├── requirements.txt
└── requirements-dev.txt
```

## 11. Terraform リソース

| リソース | 説明 |
|---------|------|
| Lambda 関数 | Python 3.12, 512MB, **10分タイムアウト** |
| DynamoDB テーブル | `cloudwatch-logs-monitor`, PAY_PER_REQUEST |
| EventBridge スケジュール | 5分間隔 |
| IAM ロール | DynamoDB / CloudWatch Logs / SNS の権限 |

**IAM ポリシーに含める権限**:
- `dynamodb:GetItem`, `dynamodb:Query`, `dynamodb:UpdateItem`, `dynamodb:PutItem`
- `logs:StartQuery`, `logs:GetQueryResults`, `logs:GetLogEvents`
- `sns:Publish`
- `ses:SendEmail`, `ses:SendRawEmail`

> **Note**: `logs:GetLogEvents` を忘れないこと（コンテキスト取得に必要）。

## 12. コスト試算

10プロジェクト × 平均10キーワード = 約100モニター。キーワード結合で10クエリ/回。

| 項目 | 月額コスト |
|------|-----------| 
| Lambda（5分×月8,640回、512MB、平均60秒） | ~$2.20 |
| DynamoDB（オンデマンド、小規模読み書き） | ~$0（無料枠内） |
| Logs Insights（10クエリ/回 × 8,640回、推定0.5GB/クエリ） | ~$3-15 |
| SNS（月数百通知想定） | ~$0（無料枠内） |
| **合計** | **~$5-17/月** |

> **Note**: Logs Insights のコストはスキャンデータ量に大きく依存。`log_stream_pattern` フィルタでスキャン範囲を限定することでコスト抑制可能。

## 13. 管理運用

### 新プロジェクト追加 → 1レコード作成

```json
{
  "pk": "PROJECT", "sk": "project-new",
  "display_name": "New Project",
  "log_stream_pattern": "project-new",
  "enabled": true,
  "monitors": [
    { "keywords": ["ERROR", "FATAL"], "severity": "critical" }
  ]
}
```

### キーワード追加 → monitors 配列に要素追加

### プロジェクト固有の通知先 → override_sns_topics を追加

### 一時停止 → `"enabled": false`

### 日次検索 → schedule / search_window を1440分に設定
