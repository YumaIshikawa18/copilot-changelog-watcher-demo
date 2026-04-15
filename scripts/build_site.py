#!/usr/bin/env python3
from __future__ import annotations

import html
import json
import logging
import re
import shutil
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import feedparser
import requests

FEED_URL = "https://github.blog/changelog/label/copilot/feed/"
SOURCE_DIR = Path("site")
OUTPUT_DIR = Path("dist")
TOKYO = ZoneInfo("Asia/Tokyo")

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


@dataclass
class FeedItem:
    title: str
    url: str
    summary: str
    published: str
    published_iso: str | None
    importance: str
    reason_ja: str
    matched_keywords: list[str]


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


def parse_published_to_iso(value: str) -> str | None:
    if not value:
        return None

    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(TOKYO).isoformat()


def has_keyword(text: str, keyword: str) -> bool:
    pattern = r"(?<![a-z0-9])" + re.escape(keyword) + r"(?![a-z0-9])"
    return re.search(pattern, text) is not None


def find_keyword_hits(text: str, keywords: tuple[str, ...]) -> list[str]:
    return [keyword for keyword in keywords if has_keyword(text, keyword)]


def classify_item(title: str, summary: str) -> tuple[str, str, list[str]]:
    source_text = f"{title} {summary}".lower()
    high_hits = find_keyword_hits(source_text, HIGH_PRIORITY_KEYWORDS)
    medium_hits = find_keyword_hits(source_text, MEDIUM_PRIORITY_KEYWORDS)
    low_hits = find_keyword_hits(source_text, LOW_PRIORITY_KEYWORDS)

    if high_hits:
        return "high", "運用やガバナンスに影響しやすいキーワードを含むため", high_hits
    if medium_hits:
        return "medium", "プレビュー公開や SDK 変更に関する更新のため", medium_hits
    if low_hits:
        return "low", "改善系の変更が中心と読み取れるため", low_hits

    return "medium", "Copilot 関連更新として確認価値があるため", []


def fetch_feed(url: str) -> list[FeedItem]:
    logging.info("RSS を取得します: %s", url)
    response = requests.get(
        url,
        headers={"User-Agent": "copilot-changelog-watcher-demo/2.0"},
        timeout=30,
    )
    response.raise_for_status()

    feed = feedparser.parse(response.content)
    if getattr(feed, "bozo", False):
        logging.warning(
            "RSS の解析で警告が発生しました: %s",
            getattr(feed, "bozo_exception", "unknown"),
        )

    items: list[FeedItem] = []
    for entry in feed.entries:
        link = clean_text(str(entry.get("link", "")))
        if not link:
            continue

        title = clean_text(str(entry.get("title", "")))
        summary = clean_text(str(entry.get("summary", "")))
        published = clean_text(str(entry.get("published", "")))
        importance, reason_ja, matched_keywords = classify_item(title, summary)

        items.append(
            FeedItem(
                title=title,
                url=link,
                summary=summary,
                published=published,
                published_iso=parse_published_to_iso(published),
                importance=importance,
                reason_ja=reason_ja,
                matched_keywords=matched_keywords,
            )
        )

    items.sort(key=lambda item: item.published_iso or "", reverse=True)
    return items


def build_payload(items: list[FeedItem]) -> dict[str, object]:
    now = datetime.now(TOKYO)
    counts = {
        "high": sum(1 for item in items if item.importance == "high"),
        "medium": sum(1 for item in items if item.importance == "medium"),
        "low": sum(1 for item in items if item.importance == "low"),
    }
    latest_published = next((item.published_iso for item in items if item.published_iso), None)

    return {
        "generated_at": now.isoformat(),
        "generated_at_label": now.strftime("%Y-%m-%d %H:%M JST"),
        "feed_url": FEED_URL,
        "item_count": len(items),
        "counts": counts,
        "latest_published": latest_published,
        "items": [
            {
                "title": item.title,
                "url": item.url,
                "summary": item.summary,
                "published": item.published,
                "published_iso": item.published_iso,
                "importance": item.importance,
                "reason_ja": item.reason_ja,
                "matched_keywords": item.matched_keywords,
            }
            for item in items
        ],
    }


def copy_site_assets(source_dir: Path, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    for source_path in source_dir.iterdir():
        target_path = output_dir / source_path.name
        if source_path.is_dir():
            shutil.copytree(source_path, target_path, dirs_exist_ok=True)
        else:
            shutil.copy2(source_path, target_path)


def write_payload(output_dir: Path, payload: dict[str, object]) -> None:
    (output_dir / "data.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (output_dir / ".nojekyll").write_text("", encoding="utf-8")


def main() -> int:
    configure_logging()

    try:
        items = fetch_feed(FEED_URL)
        payload = build_payload(items)
        copy_site_assets(SOURCE_DIR, OUTPUT_DIR)
        write_payload(OUTPUT_DIR, payload)
        logging.info("静的サイトを %s に生成しました。", OUTPUT_DIR)
        return 0
    except Exception as exc:
        logging.exception("サイト生成に失敗しました: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
