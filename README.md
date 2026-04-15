# Copilot Changelog Radar

GitHub Changelog の Copilot RSS を定期取得し、AI 要約やメール送信を使わずに、キーワードベースで `high / medium / low` に分類した静的ダッシュボードを GitHub Pages へ公開するサンプル実装です。

## 概要

- 監視対象 RSS: `https://github.blog/changelog/label/copilot/feed/`
- 分類方法: 記事タイトルと RSS 要約に含まれるキーワードで `high / medium / low` を付与
- 表示方法: GitHub Pages 上の静的サイト
- 実行方法: GitHub Actions で毎日 9:00（`Asia/Tokyo`）と手動実行
- 追加の API キー: 不要
- 追加のメール送信設定: 不要

## ディレクトリ構成

```text
.
├── .github/workflows/copilot-changelog.yml
├── requirements.txt
├── scripts/build_site.py
└── site/
    ├── app.js
    ├── index.html
    └── styles.css
```

## どう動くか

1. GitHub Changelog の Copilot RSS を取得します。
2. 記事タイトルと RSS 要約からキーワードを検出し、重要度を分類します。
3. 静的サイト用の `dist/data.json` を生成します。
4. `site/` 配下のフロント資産と合わせて GitHub Pages へデプロイします。

AI 要約は使わないため、OpenAI API の課金や応答揺れはありません。Outlook / Microsoft 365 の SMTP 設定も不要です。

## 重要度分類ルール

- `deprecated`, `retired`, `limit`, `limits`, `billing`, `admin`, `enterprise`, `metrics api`, `compliance`, `data residency` を含む場合は `high`
- `preview`, `public preview`, `ga`, `generally available`, `sdk` を含む場合は `medium`
- `improvement`, `performance`, `faster` を含む場合は `low`
- どれにも当てはまらない場合は `medium`

複数カテゴリにまたがる場合は `high > medium > low` を優先します。

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

## ローカル実行

静的サイトを生成します。

```bash
python scripts/build_site.py
```

生成物は `dist/` に出力されます。ローカルで確認する場合は簡易サーバーを立てて開きます。

```bash
python -m http.server 8000 --directory dist
```

その後、ブラウザで `http://localhost:8000` を開きます。

## GitHub Pages の設定

このワークフローは GitHub Pages の Actions デプロイを前提にしています。リポジトリ側で次を設定してください。

1. GitHub の `Settings` を開く
2. `Pages` を開く
3. `Build and deployment` の `Source` を `GitHub Actions` にする

Secrets は不要です。

## GitHub Actions 実行方法

ワークフロー定義は [copilot-changelog.yml](/home/yuzukiku/projects/copilot-changelog-watcher-demo/.github/workflows/copilot-changelog.yml) にあります。

- 定期実行: 毎日 9:00（`Asia/Tokyo`）
- 手動実行: `Actions` タブから `Copilot Changelog Watcher` を選び `Run workflow`
- デプロイ先: GitHub Pages

注意:

- `schedule` と `workflow_dispatch` は GitHub 仕様上、デフォルトブランチ上の workflow を使います
- Pages 反映には GitHub Pages の有効化が必要です

## GitHub Pages で得られるもの

- 重要度別カウント
- 記事一覧
- `high / medium / low` フィルタ
- 判定理由
- マッチしたキーワード
- 元記事リンク

## 補足

- GitHub Models や OpenAI API を使わないため、要約の自動生成は行いません
- この実装は RSS に含まれる本文要約のみを材料に分類するため、厳密な重要度判定ではなく軽量な閲覧支援です
- 依存関係は 2026-04-15 時点で確認した最新安定版に固定しています
