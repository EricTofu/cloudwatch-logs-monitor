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
| `mention` | String | (オプション) 通知文の `{mention}` 変数に埋め込まれる宛先（SlackのIDなど）。 | `"<@U12345>"` |
| `sns_topic` | String | (オプション) グローバルの `sns_topics` を無視して、強制的に送信する SNSトピックARN。 | `"arn:aws:sns:..."` |
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
| `ses_config` | Map | (オプション) このキーワード専用のEメール送信設定 |

---

## 💡 設定ファイルの階層（フォールバック）ルール

設定項目の一部（`severity`, `sns_topic`, `ses_config`, `renotify_min` など）は、上書き設定ができるよう階層化されています。評価の優先順位は次の通りです。

1. **キーワード設定 (`keywords` 内)** : 一番優先度が高い（特定のエラーごとに通知先や間隔を変える）
2. **モニター設定 (`MONITOR`)** : モニター全体への個別上書き（全エラー共通だがプロジェクト専用）
3. **全体設定 (`GLOBAL#CONFIG`)** : 上のどちらでも指定されていない場合に使われる最終的なデフォルト設定

基本的には `GLOBAL#CONFIG` にSlackへの通知ARNやデフォルトルールを記載しておき、特定の重要なエラーキーワードや特定プロジェクトだけ別の宛先に流す、といった柔軟な設定が可能です。
