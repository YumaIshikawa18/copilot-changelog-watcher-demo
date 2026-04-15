# Copilot Changelog Watcher Demo

GitHub Changelog の Copilot カテゴリ RSS を定期監視し、新着記事だけを OpenAI API で日本語要約して、重要度付きで Outlook SMTP 経由のメール通知を送るサンプル実装です。

## 概要

- 監視対象 RSS: `https://github.blog/changelog/label/copilot/feed/`
- 新着判定: `data/seen.json` に処理済み URL を保存
- 要約内容:
  - 日本語タイトル
  - 日本語要約（3〜5行）
  - 重要度（`high` / `medium` / `low`）
  - 重要度の理由
  - 対象者
  - 推奨アクション
- 通知方法: Outlook / Microsoft 365 SMTP (`smtp.office365.com:587`, STARTTLS)
- 自動実行: GitHub Actions で毎日 9:00（`Asia/Tokyo`）実行

## ディレクトリ構成

```text
.
├── .github/workflows/copilot-changelog.yml
├── data/seen.json
├── requirements.txt
└── scripts/fetch_and_summarize.py
```

## セットアップ

1. Python 3.11 を用意します。
2. 仮想環境を作成して有効化します。

```bash
python3.11 -m venv .venv
source .venv/bin/activate
```

3. 依存ライブラリをインストールします。

```bash
pip install -r requirements.txt
```

4. 環境変数を設定します。

```bash
export OPENAI_API_KEY="YOUR_OPENAI_API_KEY"
export SMTP_USER="your-account@your-domain.com"
export SMTP_PASS="your-smtp-password"
export TO_EMAIL="notify-to@your-domain.com"
```

必要に応じて、使用モデルを `OPENAI_MODEL` で上書きできます。未指定時は `gpt-4o-mini` を使用します。

```bash
export OPENAI_MODEL="gpt-4o-mini"
```

## GitHub Secrets の設定

GitHub Actions で動かす場合は、リポジトリの `Settings` -> `Secrets and variables` -> `Actions` で以下を登録してください。

- `OPENAI_API_KEY`
- `SMTP_USER`
- `SMTP_PASS`
- `TO_EMAIL`

任意で追加できる Secrets:

- `OPENAI_MODEL`

## Outlook 用 SMTP の前提

- SMTP ホストは `smtp.office365.com`
- ポートは `587`
- 暗号化は `STARTTLS`
- `SMTP_USER` には送信元メールアドレスを指定します
- Microsoft 365 / Exchange Online 側で SMTP AUTH が利用可能である必要があります

この初期実装は要件に合わせて `SMTP_USER` / `SMTP_PASS` による SMTP ログインを行います。ただし、Exchange Online では Security Defaults や認証ポリシー次第で SMTP AUTH / Basic 認証が既に使えない場合があります。さらに Microsoft の 2026-01-27 更新では、SMTP AUTH の Basic 認証は 2026 年末に既存テナントで既定無効、2027 年後半に最終廃止日を別途案内予定とされています。実運用では、利用テナントの設定確認に加えて、OAuth 対応や SMTP リレー、Microsoft Graph API への移行を検討してください。

## ローカル実行方法

環境変数を設定したうえで、次を実行します。

```bash
python scripts/fetch_and_summarize.py
```

挙動:

- RSS を取得して `data/seen.json` の既知 URL を除外
- 新着が 1 件以上あれば OpenAI API で日本語要約を生成
- 記事内キーワードに応じて重要度を軽く補正
- まとめて 1 通のメールを送信
- メール送信成功後に `data/seen.json` を更新

新着が 0 件の場合はメール送信せず、そのまま正常終了します。

## GitHub Actions 実行方法

ワークフロー定義は [copilot-changelog.yml](/home/yuzukiku/projects/copilot-changelog-watcher-demo/.github/workflows/copilot-changelog.yml) にあります。

- 定期実行: 毎日 9:00（`Asia/Tokyo`）
- 手動実行: GitHub の `Actions` タブから `Copilot Changelog Watcher` を選び、`Run workflow`
- 実行後、`data/seen.json` に変更があれば自動で commit / push
- エラー時は workflow ログに Python スクリプトの例外ログを出力

## 重要度補正ロジック

OpenAI の判定をベースにしつつ、以下のキーワードを記事タイトル・要約・生成結果から検出して最終重要度を軽く補正します。

- `deprecated`, `retired`, `limit`, `limits`, `billing`, `admin`, `enterprise`, `metrics api`, `compliance`, `data residency` は `high` 寄り
- `preview`, `public preview`, `ga`, `generally available`, `sdk` は `medium` 寄り
- `improvement`, `performance`, `faster` は `low` 寄り

補正が入った場合は、メール本文の「理由」に補正メモも追記されます。

## メール本文フォーマット

記事ごとに次の形式で 1 通にまとめて送信します。

```text
---
タイトル: {title_ja}
重要度: {importance}

要約:
{summary_ja}

理由:
{reason_ja}

対象者:
{audience_ja を読める形で結合}

推奨アクション:
{action_ja}

元記事:
{url}
---
```

件名例:

```text
[Copilot Changelog] 新着 3件（high:1 / medium:1 / low:1）
```

## 補足

- `data/seen.json` は URL のみを保持する単純な構成です
- 依存関係は 2026-04-15 時点の最新安定版に固定しています
- 初期実装として、過度な抽象化は避けて読みやすさを優先しています
