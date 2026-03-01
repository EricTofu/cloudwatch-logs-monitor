# CloudWatch Logs Monitor

DynamoDB 設定ベースの CloudWatch Logs 監視システム。Logs Insights を使ったキーワード検出、Slack（AWS Chatbot）と Email への通知を提供します。

## 特徴

- **マルチプロジェクト対応** — 複数プロジェクトのログを1つの Lambda で監視
- **DynamoDB 設定管理** — Web コンソールや CLI でリアルタイムに設定変更可能
- **Logs Insights** — Infrequent Access クラスのログにも対応
- **キーワード結合クエリ** — プロジェクト単位で1クエリにまとめ、コストとクエリ数を削減
- **3 段階フォールバック** — 通知先・テンプレートを MONITOR → PROJECT → GLOBAL で解決
- **状態管理** — ALARM / OK 状態追跡、再通知間隔制御、復旧通知
- **エラー分離** — 1 プロジェクトの障害が他のプロジェクトに影響しない

## アーキテクチャ

```
EventBridge (5分) → Lambda → Logs Insights (クエリ)
                       ↓
                   DynamoDB (設定 & 状態管理)
                       ↓
                   SNS → Chatbot → Slack
                   SNS → Email
```

## プロジェクト構成

```
├── src/log_monitor/       # Lambda ソースコード
│   ├── handler.py         # エントリーポイント
│   ├── config.py          # DynamoDB 設定読み込み
│   ├── query.py           # Logs Insights クエリ構築・実行
│   ├── context.py         # 前後ログ行の取得
│   ├── exclusion.py       # 除外パターンフィルタリング
│   ├── state.py           # 状態遷移ロジック
│   ├── notifier.py        # SNS 通知（Chatbot / Email）
│   └── constants.py       # 定数・boto3 クライアント
├── tests/                 # テストスイート (58 cases)
├── terraform/             # インフラ定義
├── scripts/               # DynamoDB 初期データ投入
├── DESIGN.md              # 設計ドキュメント
└── API_REFERENCE.md       # 関数一覧
```

## セットアップ

### 前提条件

- Python 3.12+
- [uv](https://docs.astral.sh/uv/)
- Terraform 1.0+
- AWS CLI（プロファイル設定済み）

### 開発環境

```bash
# 依存インストール
uv sync --extra dev

# テスト実行
uv run pytest tests/ -v

# カバレッジ付き
uv run pytest tests/ -v --cov=src/log_monitor --cov-report=term-missing

# Lint
uv run ruff check src/ tests/

# Lint 自動修正
uv run ruff check src/ tests/ --fix
```

### デプロイ

```bash
cd terraform

terraform init
terraform plan -var="aws_profile=your-profile-name"
terraform apply -var="aws_profile=your-profile-name"
```

### DynamoDB 初期データ投入

```bash
uv run python scripts/seed_dynamodb.py your-profile-name
```

## 設定例

### 最小構成（PROJECT レコード）

```json
{
  "pk": "PROJECT", "sk": "my-project",
  "display_name": "My Project",
  "log_stream_pattern": "my-project",
  "enabled": true,
  "monitors": [
    { "keywords": ["ERROR", "FATAL"], "severity": "critical" }
  ]
}
```

### 詳細設定

設定の詳細は [DESIGN.md](DESIGN.md) のセクション 4 を参照してください。関数の一覧は [API_REFERENCE.md](API_REFERENCE.md) を参照してください。

## テスト

AWS 環境なしで [moto](https://github.com/getmoto/moto) を使ったモックテストを実行します。

| モジュール | モック方式 | テスト内容 |
|-----------|----------|-----------|
| `config.py` | moto (DynamoDB) | CRUD 操作、設定マージ |
| `query.py` | unittest.mock | クエリ構築、結果振り分け |
| `exclusion.py` | モック不要 | 正規表現フィルタリング |
| `state.py` | モック不要 | 状態遷移 (6 アクション) |
| `notifier.py` | moto (SNS) | 通知送信、テンプレート展開 |
| `handler.py` | moto + mock | エンドツーエンド統合テスト |

## ライセンス

MIT
