# DynamoDB 設定リファレンス (CloudWatch Logs Monitor)

当システムでは、DynamoDB テーブルを用いて全体設定および各監視プロジェクト（モニター）の設定を管理しています。

主なレコードは以下の2種類に分かれます。
1. **全体設定 (`GLOBAL#CONFIG`)**: システム全体のデフォルト値や、共通の通知先などを定義します。
2. **モニター設定 (`MONITOR#{監視ID}`)**: プロジェクトごとのロググループ、検索クエリ、キーワードなどを定義します。

---

## 1. 全体設定 (pk: `GLOBAL`, sk: `CONFIG`)
全体設定のレコードは、システムに1つだけ存在します。

| プロパティ名 | 型 | 説明 | 設定例 |
| :--- | :--- | :--- | :--- |
| `pk` | String | パーティションキー。常に `GLOBAL` を指定します。 | `"GLOBAL"` |
| `sk` | String | ソートキー。常に `CONFIG` を指定します。 | `"CONFIG"` |
| `defaults` | Map | モニター側で設定が省略された場合に使われる**デフォルト値のセット**。<br>※ 詳細は後述の表を参照。 | `{"severity": "warning", ...}` |
| `sns_topics` | Map | 深刻度（`critical` や `warning` など）に応じた SNS トピック(Slack等)への ARN マッピング。 | `{"critical": "arn:aws:sns:..."}` |
| `ses_config` | Map | Eメール送信用のSES設定。(送信元、返信先、深刻度別の宛先リスト) | `{"from_address": "...", "recipients": {"critical": [...]}}` |
| `notification_template`| Map | アラーム検知時の通知テンプレート。(`subject`, `body` を含む) | `{"subject": "[{severity}] ...", ...}` |
| `recover_template` | Map | アラーム回復時の通知テンプレート。(`subject`, `body` を含む) | `{"subject": "[RECOVER] ...", ...}` |

### `defaults` 内の記述項目
| 項目名 | 定義内容 |
| :--- | :--- |
| `severity` | 深刻度のデフォルト値 (基本は `"warning"` など) |
| `search_window_minutes`| 何分前までのログをクエリで検索するかのデフォルト分数 |
| `context_lines` | エラーログの前後何行を取得するかのデフォルト行数 |
| `renotify_min` | 同じエラーが出続けた際、次に再通知を行うまでの待機時間(分)。無効化は `"disabled"` |
| `notify_on_recover` | エラーが収束した時に回復通知（RECOVER）を送信するかどうか。`true`/`false` |
| `display_timezone` | 通知メッセージ中のタイムスタンプ表示に使うタイムゾーン。デフォルトは `"Asia/Tokyo"` |

---

## 2. モニター設定 (pk: `MONITOR`, sk: `{monitor_id}`)
プロジェクトごと、または監視対象ごとに作成するレコードです。

| プロパティ名 | 型 | 説明 | 設定例 |
| :--- | :--- | :--- | :--- |
| `pk` | String | パーティションキー。常に `MONITOR` を指定します。 | `"MONITOR"` |
| `sk` | String | ソートキー。一意のモニターIDを指定します。 | `"project-a"` |
| `display_name` | String | 通知などで表示されるための人間が読みやすい表示名。 | `"Project Alpha"` |
| `log_group` | String | 監視対象となる CloudWatch Logs のロググループ名。 | `"/aws/app/shared-logs"` |
| `query` | String | Logs Insights で実行される検索クエリ文字列。 | `"fields @timestamp ..."` |
| `enabled` | Boolean | `true` であれば監視有効、`false` ならスキップされます。 | `true` / `false` |
| `keywords` | List(Map) | (オプション) 検索結果からさらに検知すべきキーワードグループのリスト。※後述 | `[{"words": ["ERROR"], ...}]` |
| `search_window_minutes`| Number | (オプション) このモニター専用の検索期間（分）。省略時は `defaults` を使用。 | `10` |
| `context_lines` | Number | (オプション) このモニター専用のコンテキスト取得行数。 | `5` |
| `severity` | String | (オプション) モニター全体に適用される深刻度。 | `"critical"` |
| `renotify_min` | Num/Str| (オプション) 再通知間隔。無効化は `"disabled"` を指定。 | `30` |
| `notify_on_recover` | Boolean| (オプション) 回復通知のオン・オフ上書き。 | `true` |
| `display_timezone` | String | (オプション) このモニター専用のタイムスタンプ表示用タイムゾーン。 | `"UTC"` |
| `mention` | String | (オプション) 通知文の `{mention}` 変数に埋め込まれる宛先（SlackのIDなど）。 | `"<@U12345>"` |
| `sns_topic` | String | (オプション) グローバルの `sns_topics` を無視して、強制的に送信する SNSトピックARN。 | `"arn:aws:sns:..."` |
| `sns_topics` | Map | (オプション) モニター専用の深刻度に応じた SNS トピックのマッピング。 | `{"critical": "arn...", "warning": "arn..."}` |
| `ses_config` | Map | (オプション) このモニター専用のEメール送信設定のオーバーライド。 | `{"recipients": [...]}` |

### `keywords` 配列内の記述項目
指定したクエリの検索結果の中で、さらに種類ごとにエラーを分類・フィルタリングしたい場合に定義します。（未指定の場合は、検索結果すべてが共通のアラームとして処理されます）

| 項目名 | 型 | 説明 |
| :--- | :--- | :--- |
| `words` | List(Str) | 検知対象となるキーワードのリスト (例: `["ERROR", "FATAL"]`) |
| `severity` | String | (オプション) このキーワードグループが検知された場合の深刻度 |
| `renotify_min` | Num/Str| (オプション) このキーワードに対する再通知間隔の上書き |
| `mention` | String | (オプション) このキーワード検知時専用のメンション |
| `sns_topic` | String | (オプション) このキーワード検知時専用の強制送信 SNSトピックARN |
| `sns_topics` | Map | (オプション) このキーワード専用の深刻度に応じた SNS トピックのマッピング |
| `ses_config` | Map | (オプション) このキーワード専用のEメール送信設定 |

---

## 💡 設定ファイルの階層（フォールバック）ルール

設定項目の一部（`severity`, `sns_topic(s)`, `ses_config`, `renotify_min` など）は、上書き設定ができるよう階層化されています。Slackなどの通知先（SNS トピック）の評価の優先順位は次の通りです。

1. **キーワードの `sns_topic` (文字列)** : 最も優先され、深刻度を無視して強制的に指定のARNに送信します。
2. **キーワードの `sns_topics` (Map)** : 指定された深刻度(`severity`)に対応するARNがあればそれに送信します。
3. **モニターの `sns_topic` (文字列)** : キーワードで未指定の場合、モニター全体での強制送信先として使用されます。
4. **モニターの `sns_topics` (Map)** : キーワードで未指定の場合、モニター専用の深刻度マッピングが使用されます。
5. **全体設定の `sns_topics` (Map)** : 最終的なデフォルト設定です。

1. **キーワード設定 (`keywords` 内)** : 一番優先度が高い（特定のエラーごとに通知先や間隔を変える）
2. **モニター設定 (`MONITOR`)** : モニター全体への個別上書き（全エラー共通だがプロジェクト専用）
3. **全体設定 (`GLOBAL#CONFIG`)** : 上のどちらでも指定されていない場合に使われる最終的なデフォルト設定

基本的には `GLOBAL#CONFIG` にSlackへの通知ARNやデフォルトルールを記載しておき、特定の重要なエラーキーワードや特定プロジェクトだけ別の宛先に流す、といった柔軟な設定が可能です。

---

## 3. CloudWatch Logs Insights クエリの書き方

モニター設定の `query` フィールドに記述する Logs Insights クエリの構文です。

### 基本構造

```
fields @timestamp, @message, @logStream
| filter @logStream like /project-a/
| filter @message like /ERROR/
| sort @timestamp asc
| limit 500
```

> **重要**: 当システムでは `@timestamp`, `@message`, `@logStream` の3フィールドを使用するため、`fields` には必ずこの3つを含めてください。

### フィルタリング（包含）

```
# 単一キーワード
| filter @message like /ERROR/

# 複数キーワード（OR）
| filter (@message like /ERROR/ or @message like /FATAL/ or @message like /TIMEOUT/)

# 正規表現でまとめて記述
| filter @message like /ERROR|FATAL|TIMEOUT/

# ログストリームでフィルタ
| filter @logStream like /project-a/
```

### フィルタリング（除外）

```
# 特定文字列を除外
| filter @message not like /HealthCheck/

# 複数の除外
| filter @message not like /HealthCheck/
    and @message not like /heartbeat/

# 正規表現で複数パターンをまとめて除外
| filter @message not like /HealthCheck|heartbeat|ping/

# 完全一致で除外
| filter @message != "specific exact message"
```

### ⚠️ 正規表現の特殊文字エスケープ

`like` / `not like` の `/パターン/` 内は**正規表現**として解釈されます。以下の文字はエスケープが必要です。

| 文字 | エスケープ | 説明 |
| :--- | :--- | :--- |
| `[` | `\[` | 文字クラスの開始 |
| `]` | `\]` | 文字クラスの終了 |
| `(` | `\(` | グループの開始 |
| `)` | `\)` | グループの終了 |
| `.` | `\.` | 任意の1文字 |
| `*` | `\*` | 前の文字の0回以上 |
| `+` | `\+` | 前の文字の1回以上 |
| `?` | `\?` | 前の文字の0回または1回 |
| `{` | `\{` | 量指定子 |
| `\` | `\\` | エスケープ文字自体 |

**よくある例: JSON の角括弧を含むパターン**

```
# ❌ NG: [] が正規表現の「空の文字クラス」として解釈される
| filter @message not like /"failed_results":[]/

# ✅ OK: 角括弧をエスケープ
| filter @message not like /"failed_results":\[\]/
```

### クエリ例

**基本的なエラー監視（5分ごと）:**
```
fields @timestamp, @message, @logStream
| filter @logStream like /project-a/
| filter (@message like /ERROR/ or @message like /FATAL/)
| sort @timestamp asc
| limit 500
```

**除外パターン付き（特定の既知エラーをスキップ）:**
```
fields @timestamp, @message, @logStream
| filter @message like /ERROR/
| filter @message not like /HealthCheck/
| filter @message not like /"failed_results":\[\]/
| sort @timestamp asc
| limit 500
```

**日次バッチ監視（`search_window_minutes: 1450` と併用）:**
```
fields @timestamp, @message, @logStream
| filter @message like /ERROR|WARN/
| sort @timestamp asc
| limit 1000
```

---

## 4. 通知テンプレート変数

`notification_template` および `recover_template` の `subject` / `body` で使用できる変数です。

| 変数 | 説明 | 例 |
| :--- | :--- | :--- |
| `{display_name}` | モニターの表示名 | `Project Alpha` |
| `{keyword}` | 検知されたキーワード | `ERROR` |
| `{severity}` | 深刻度（大文字） | `CRITICAL` |
| `{count}` | 検知されたログ行数 | `5` |
| `{detected_at}` | 検知日時（JST） | `2026-03-05 15:00:00 JST` |
| `{log_group}` | 対象ロググループ名 | `/aws/app/shared-logs` |
| `{stream_name}` | ログストリーム名（最大3つ） | `stream-1` |
| `{log_lines}` | 検知されたログ行（ページ分割済み） | `[1] 2026-03-05T15:00:00 ERROR ...` |
| `{context_lines}` | エラー前後のコンテキスト行 | `── [Context for Log 1] ──` |
| `{fingerprint}` | ログメッセージのフィンガープリント | `a1b2c3d4` |
| `{original_message}` | 最初に検知された元のログメッセージ | `ERROR: Connection refused` |
| `{mention}` | メンション先（Slack IDなど） | `<@U12345>` |

---

## 5. Slack (Chatbot) 通知のページネーション

AWS Chatbot の `description` フィールドは **4096文字制限** があります。ログ行が多い場合、システムは自動的にページ分割して送信します。

- 各ページにログ本文と対応するコンテキストが含まれます（ログ本文とコンテキストが別ページに分かれることはありません）
- コンテキストが非常に大きい場合、そのエントリのコンテキストが truncate されます

