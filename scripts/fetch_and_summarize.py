#!/usr/bin/env python3
from __future__ import annotations

import html
import json
import logging
import os
import re
import smtplib
import ssl
import sys
from collections import Counter
from dataclasses import dataclass
from email.message import EmailMessage
from pathlib import Path
from typing import Any

import feedparser
import requests
from openai import OpenAI

FEED_URL = "https://github.blog/changelog/label/copilot/feed/"
SMTP_HOST = "smtp.office365.com"
SMTP_PORT = 587
DEFAULT_MODEL = "gpt-4o-mini"
SEEN_FILE = Path("data/seen.json")

HIGH_PRIORITY_KEYWORDS = (
    "deprecated",
    "retired",
    "limit",
    "limits",
    "billing",
    "admin",
    "enterprise",
    "metrics api",
    "compliance",
    "data residency",
)
MEDIUM_PRIORITY_KEYWORDS = (
    "preview",
    "public preview",
    "ga",
    "generally available",
    "sdk",
)
LOW_PRIORITY_KEYWORDS = (
    "improvement",
    "performance",
    "faster",
)

TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"\s+")

IMPORTANCE_TO_SCORE = {"low": 0, "medium": 1, "high": 2}
SCORE_TO_IMPORTANCE = {score: level for level, score in IMPORTANCE_TO_SCORE.items()}


@dataclass
class FeedItem:
    title: str
    url: str
    summary: str
    published: str


@dataclass
class ProcessedItem:
    source: FeedItem
    title_ja: str
    summary_ja: str
    importance: str
    reason_ja: str
    audience_ja: list[str]
    action_ja: str


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def clean_text(value: str) -> str:
    without_tags = TAG_RE.sub(" ", value or "")
    unescaped = html.unescape(without_tags)
    return WS_RE.sub(" ", unescaped).strip()


def load_seen_urls(path: Path) -> set[str]:
    if not path.exists():
        return set()

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        logging.warning("%s の JSON 解析に失敗したため、空として扱います。", path)
        return set()

    if isinstance(payload, list):
        return {str(url) for url in payload if isinstance(url, str)}

    if isinstance(payload, dict):
        seen_urls = payload.get("seen_urls", [])
        if isinstance(seen_urls, list):
            return {str(url) for url in seen_urls if isinstance(url, str)}

    logging.warning("%s の形式が想定外だったため、空として扱います。", path)
    return set()


def save_seen_urls(path: Path, seen_urls: set[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"seen_urls": sorted(seen_urls)}
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def fetch_feed(url: str) -> list[FeedItem]:
    logging.info("RSS を取得します: %s", url)
    response = requests.get(
        url,
        headers={"User-Agent": "copilot-changelog-watcher-demo/1.0"},
        timeout=30,
    )
    response.raise_for_status()

    feed = feedparser.parse(response.content)
    if getattr(feed, "bozo", False):
        logging.warning("RSS の解析で警告が発生しました: %s", getattr(feed, "bozo_exception", "unknown"))

    items: list[FeedItem] = []
    for entry in feed.entries:
        link = str(entry.get("link", "")).strip()
        if not link:
            continue

        items.append(
            FeedItem(
                title=clean_text(str(entry.get("title", ""))),
                url=link,
                summary=clean_text(str(entry.get("summary", ""))),
                published=clean_text(str(entry.get("published", ""))),
            )
        )
    return items


def select_new_items(items: list[FeedItem], seen_urls: set[str]) -> list[FeedItem]:
    new_items: list[FeedItem] = []
    queued_urls: set[str] = set()

    for item in items:
        if item.url in seen_urls or item.url in queued_urls:
            continue
        new_items.append(item)
        queued_urls.add(item.url)

    return list(reversed(new_items))


def require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"環境変数 {name} が設定されていません。")
    return value


def get_summary_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "title_ja": {"type": "string"},
            "summary_ja": {"type": "string"},
            "importance": {"type": "string", "enum": ["high", "medium", "low"]},
            "reason_ja": {"type": "string"},
            "audience_ja": {
                "type": "array",
                "items": {"type": "string"},
            },
            "action_ja": {"type": "string"},
        },
        "required": [
            "title_ja",
            "summary_ja",
            "importance",
            "reason_ja",
            "audience_ja",
            "action_ja",
        ],
    }


def summarize_item(client: OpenAI, model: str, item: FeedItem) -> ProcessedItem:
    logging.info("OpenAI で要約します: %s", item.url)
    response = client.responses.create(
        model=model,
        input=[
            {
                "role": "system",
                "content": (
                    "You summarize GitHub Copilot changelog updates for Japanese business and engineering readers. "
                    "Return concise Japanese. summary_ja must be 3 to 5 short lines. "
                    "importance must be exactly one of high, medium, low. "
                    "audience_ja must be a JSON array of Japanese role labels."
                ),
            },
            {
                "role": "user",
                "content": (
                    "次の GitHub Changelog Copilot 記事を分析し、日本語で要約してください。\n\n"
                    f"URL: {item.url}\n"
                    f"公開日時: {item.published or '不明'}\n"
                    f"原文タイトル: {item.title}\n"
                    f"RSS 要約: {item.summary or 'なし'}\n\n"
                    "重要度は管理者・導入担当・開発者にとっての影響で判定してください。"
                ),
            },
        ],
        max_output_tokens=700,
        text={
            "format": {
                "type": "json_schema",
                "name": "copilot_changelog_summary",
                "strict": True,
                "schema": get_summary_schema(),
            }
        },
    )

    if not response.output_text:
        raise RuntimeError(f"OpenAI の応答が空でした: {item.url}")

    payload = json.loads(response.output_text)
    summary = normalize_summary_payload(payload)
    return apply_importance_correction(item, summary)


def normalize_summary_payload(payload: dict[str, Any]) -> ProcessedItem:
    importance = str(payload["importance"]).lower().strip()
    if importance not in IMPORTANCE_TO_SCORE:
        raise ValueError(f"importance が不正です: {importance}")

    audience = payload.get("audience_ja", [])
    audience_items = [str(entry).strip() for entry in audience if str(entry).strip()]

    return ProcessedItem(
        source=FeedItem(title="", url="", summary="", published=""),
        title_ja=str(payload["title_ja"]).strip(),
        summary_ja=str(payload["summary_ja"]).strip(),
        importance=importance,
        reason_ja=str(payload["reason_ja"]).strip(),
        audience_ja=audience_items or ["開発者"],
        action_ja=str(payload["action_ja"]).strip(),
    )


def has_keyword(text: str, keyword: str) -> bool:
    pattern = r"(?<![a-z0-9])" + re.escape(keyword) + r"(?![a-z0-9])"
    return re.search(pattern, text) is not None


def find_keyword_hits(text: str, keywords: tuple[str, ...]) -> list[str]:
    return [keyword for keyword in keywords if has_keyword(text, keyword)]


def apply_importance_correction(item: FeedItem, summary: ProcessedItem) -> ProcessedItem:
    base_score = IMPORTANCE_TO_SCORE[summary.importance]
    source_text = " ".join(
        [
            item.title,
            item.summary,
            summary.title_ja,
            summary.summary_ja,
            summary.reason_ja,
            summary.action_ja,
        ]
    ).lower()

    high_hits = find_keyword_hits(source_text, HIGH_PRIORITY_KEYWORDS)
    medium_hits = find_keyword_hits(source_text, MEDIUM_PRIORITY_KEYWORDS)
    low_hits = find_keyword_hits(source_text, LOW_PRIORITY_KEYWORDS)

    adjusted_score = base_score
    if high_hits and not low_hits:
        adjusted_score = min(2, adjusted_score + 1)
    elif low_hits and not high_hits:
        adjusted_score = max(0, adjusted_score - 1)

    if medium_hits:
        if adjusted_score < 1:
            adjusted_score += 1
        elif adjusted_score > 1:
            adjusted_score -= 1

    adjusted_importance = SCORE_TO_IMPORTANCE[adjusted_score]
    reason_suffix = build_correction_note(base_score, adjusted_score, high_hits, medium_hits, low_hits)
    if reason_suffix:
        summary.reason_ja = f"{summary.reason_ja} {reason_suffix}".strip()

    summary.source = item
    summary.importance = adjusted_importance
    return summary


def build_correction_note(
    base_score: int,
    adjusted_score: int,
    high_hits: list[str],
    medium_hits: list[str],
    low_hits: list[str],
) -> str:
    if adjusted_score == base_score:
        return ""

    notes: list[str] = []
    if high_hits:
        notes.append(f"high 補正キーワード: {', '.join(high_hits)}")
    if medium_hits:
        notes.append(f"medium 補正キーワード: {', '.join(medium_hits)}")
    if low_hits:
        notes.append(f"low 補正キーワード: {', '.join(low_hits)}")

    adjusted_importance = SCORE_TO_IMPORTANCE[adjusted_score]
    return f"キーワード補正により最終重要度を {adjusted_importance} としました（{' / '.join(notes)}）。"


def build_subject(items: list[ProcessedItem]) -> str:
    counts = Counter(item.importance for item in items)
    return (
        f"[Copilot Changelog] 新着 {len(items)}件"
        f"（high:{counts.get('high', 0)} / medium:{counts.get('medium', 0)} / low:{counts.get('low', 0)}）"
    )


def build_body(items: list[ProcessedItem]) -> str:
    blocks: list[str] = []
    for item in items:
        audience = "、".join(item.audience_ja)
        blocks.append(
            "\n".join(
                [
                    "---",
                    f"タイトル: {item.title_ja}",
                    f"重要度: {item.importance}",
                    "",
                    "要約:",
                    item.summary_ja,
                    "",
                    "理由:",
                    item.reason_ja,
                    "",
                    "対象者:",
                    audience,
                    "",
                    "推奨アクション:",
                    item.action_ja,
                    "",
                    "元記事:",
                    item.source.url,
                    "---",
                ]
            )
        )
    return "\n\n".join(blocks)


def send_email(subject: str, body: str, smtp_user: str, smtp_pass: str, to_email: str) -> None:
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = smtp_user
    message["To"] = to_email
    message.set_content(body)

    logging.info("Outlook SMTP でメールを送信します: %s", to_email)
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as smtp:
        smtp.ehlo()
        smtp.starttls(context=ssl.create_default_context())
        smtp.ehlo()
        smtp.login(smtp_user, smtp_pass)
        smtp.send_message(message)


def main() -> int:
    configure_logging()

    try:
        seen_urls = load_seen_urls(SEEN_FILE)
        feed_items = fetch_feed(FEED_URL)
        new_items = select_new_items(feed_items, seen_urls)

        if not new_items:
            logging.info("新着記事はありません。メール送信は行いません。")
            return 0

        api_key = require_env("OPENAI_API_KEY")
        smtp_user = require_env("SMTP_USER")
        smtp_pass = require_env("SMTP_PASS")
        to_email = require_env("TO_EMAIL")
        model = os.getenv("OPENAI_MODEL") or DEFAULT_MODEL

        client = OpenAI(api_key=api_key)
        processed_items = [summarize_item(client, model, item) for item in new_items]

        subject = build_subject(processed_items)
        body = build_body(processed_items)
        send_email(subject, body, smtp_user, smtp_pass, to_email)

        updated_seen_urls = seen_urls | {item.url for item in new_items}
        save_seen_urls(SEEN_FILE, updated_seen_urls)
        logging.info("処理済み URL を %s に保存しました。", SEEN_FILE)
        return 0
    except Exception as exc:
        logging.exception("処理に失敗しました: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
