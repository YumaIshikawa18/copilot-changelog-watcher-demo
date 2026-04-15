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
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qs, urljoin, urlparse
from zoneinfo import ZoneInfo

import feedparser
import requests

FEED_URL = "https://github.blog/changelog/label/copilot/feed/"
LIST_URL = "https://github.blog/changelog/label/copilot/"
MAX_ITEMS = 50
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
    changelog_type: str
    tags: list[str]
    importance: str
    reason_ja: str
    matched_keywords: list[str]


class ChangelogPageParser(HTMLParser):
    def __init__(self, page_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.page_url = page_url
        self.older_url: str | None = None
        self.items: list[dict[str, object]] = []
        self._in_article = False
        self._capture_title = False
        self._capture_date = False
        self._capture_type = False
        self._capture_tag = False
        self._current_item: dict[str, object] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        classes = set((attributes.get("class") or "").split())

        if tag == "article":
            self._in_article = True
            self._current_item = {
                "title": "",
                "url": "",
                "published": "",
                "published_iso": None,
                "changelog_type": "",
                "tags": [],
            }
            return

        if not self._in_article or self._current_item is None:
            if tag == "a" and "ChangelogPagination-next" in classes:
                href = attributes.get("href")
                if href and not self.older_url:
                    self.older_url = urljoin(self.page_url, href)
            return

        if tag == "time":
            self._capture_date = True
            datetime_value = attributes.get("datetime") or ""
            self._current_item["published_iso"] = parse_date_only_to_iso(datetime_value)
            return

        if tag == "span" and "Tag--type-alt" in classes:
            self._capture_type = True
            return

        if tag == "a" and "ChangelogItem-title" in classes:
            self._capture_title = True
            self._current_item["url"] = urljoin(self.page_url, attributes.get("href") or "")
            return

        if tag == "a" and "Tag" in classes:
            href = attributes.get("href") or ""
            if href and not is_copilot_label_url(self.page_url, href):
                self._capture_tag = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "article":
            if self._current_item:
                title = clean_text(str(self._current_item["title"]))
                url = clean_text(str(self._current_item["url"]))
                if title and url:
                    self.items.append(
                        {
                            "title": title,
                            "url": url,
                            "published": clean_text(str(self._current_item["published"])),
                            "published_iso": self._current_item["published_iso"],
                            "changelog_type": clean_text(str(self._current_item["changelog_type"])),
                            "tags": [
                                clean_text(str(tag_name))
                                for tag_name in list(self._current_item["tags"])
                                if clean_text(str(tag_name))
                            ],
                        }
                    )
            self._in_article = False
            self._capture_title = False
            self._capture_date = False
            self._capture_type = False
            self._capture_tag = False
            self._current_item = None
            return

        if tag == "time":
            self._capture_date = False
        elif tag == "span":
            self._capture_type = False
        elif tag == "a":
            self._capture_title = False
            self._capture_tag = False

    def handle_data(self, data: str) -> None:
        if not self._in_article or self._current_item is None:
            return

        if self._capture_title:
            self._current_item["title"] = f"{self._current_item['title']}{data}"
        elif self._capture_date:
            self._current_item["published"] = f"{self._current_item['published']}{data}"
        elif self._capture_type:
            self._current_item["changelog_type"] = f"{self._current_item['changelog_type']}{data}"
        elif self._capture_tag:
            tags = self._current_item["tags"]
            if isinstance(tags, list):
                tags.append(data)


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


def is_copilot_label_url(base_url: str, href: str) -> bool:
    absolute_url = urljoin(base_url, href)
    parsed = urlparse(absolute_url)
    query_labels = [value.lower() for value in parse_qs(parsed.query).get("label", [])]

    if "copilot" in query_labels:
        return True

    return parsed.path.rstrip("/") == urlparse(LIST_URL).path.rstrip("/")


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


def parse_date_only_to_iso(value: str) -> str | None:
    if not value:
        return None

    try:
        parsed = datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=TOKYO)
    except ValueError:
        return None

    return parsed.isoformat()


def has_keyword(text: str, keyword: str) -> bool:
    pattern = r"(?<![a-z0-9])" + re.escape(keyword) + r"(?![a-z0-9])"
    return re.search(pattern, text) is not None


def find_keyword_hits(text: str, keywords: tuple[str, ...]) -> list[str]:
    return [keyword for keyword in keywords if has_keyword(text, keyword)]


def classify_item(
    title: str,
    summary: str,
    changelog_type: str,
    tags: list[str],
) -> tuple[str, str, list[str]]:
    source_text = " ".join([title, summary, changelog_type, " ".join(tags)]).lower()

    if changelog_type.lower() == "retired":
        retired_hits = find_keyword_hits(source_text, HIGH_PRIORITY_KEYWORDS)
        return "high", "Retired 系の変更として影響確認が必要なため", retired_hits or ["retired"]

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


def fetch_rss_entries(url: str) -> dict[str, dict[str, str | None]]:
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

    items_by_url: dict[str, dict[str, str | None]] = {}
    for entry in feed.entries:
        link = clean_text(str(entry.get("link", "")))
        if not link:
            continue

        published = clean_text(str(entry.get("published", "")))
        items_by_url[link] = {
            "summary": clean_text(str(entry.get("summary", ""))),
            "published": published,
            "published_iso": parse_published_to_iso(published),
        }

    return items_by_url


def fetch_changelog_items(list_url: str, max_items: int) -> list[dict[str, object]]:
    logging.info("Changelog 一覧を取得します: %s", list_url)
    items: list[dict[str, object]] = []
    seen_pages: set[str] = set()
    seen_urls: set[str] = set()
    next_url: str | None = list_url

    while next_url and len(items) < max_items:
        if next_url in seen_pages:
            logging.warning("同じ一覧ページを再訪しようとしたため巡回を停止します: %s", next_url)
            break
        seen_pages.add(next_url)

        response = requests.get(
            next_url,
            headers={"User-Agent": "copilot-changelog-watcher-demo/2.0"},
            timeout=30,
        )
        response.raise_for_status()

        parser = ChangelogPageParser(next_url)
        parser.feed(response.text)

        for item in parser.items:
            url = str(item.get("url", ""))
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)
            items.append(item)
            if len(items) >= max_items:
                break

        next_url = parser.older_url

    return items[:max_items]


def build_items(max_items: int) -> list[FeedItem]:
    rss_by_url = fetch_rss_entries(FEED_URL)
    changelog_items = fetch_changelog_items(LIST_URL, max_items)

    items: list[FeedItem] = []
    for item in changelog_items:
        url = str(item["url"])
        rss_entry = rss_by_url.get(url, {})
        title = str(item["title"])
        summary = clean_text(str(rss_entry.get("summary", "")))
        published = clean_text(str(rss_entry.get("published", "") or item["published"]))
        published_iso = str(rss_entry.get("published_iso") or item["published_iso"] or "")
        changelog_type = clean_text(str(item["changelog_type"]))
        tags = [
            clean_text(str(tag_name))
            for tag_name in list(item.get("tags", []))
            if clean_text(str(tag_name))
        ]
        importance, reason_ja, matched_keywords = classify_item(title, summary, changelog_type, tags)

        items.append(
            FeedItem(
                title=title,
                url=url,
                summary=summary,
                published=published or clean_text(str(item["published"])),
                published_iso=published_iso or None,
                changelog_type=changelog_type,
                tags=tags,
                importance=importance,
                reason_ja=reason_ja,
                matched_keywords=matched_keywords,
            )
        )

    items.sort(key=lambda current_item: current_item.published_iso or "", reverse=True)
    return items


def build_payload(items: list[FeedItem], max_items: int) -> dict[str, object]:
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
        "max_items": max_items,
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
                "changelog_type": item.changelog_type,
                "tags": item.tags,
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
        items = build_items(MAX_ITEMS)
        payload = build_payload(items, MAX_ITEMS)
        copy_site_assets(SOURCE_DIR, OUTPUT_DIR)
        write_payload(OUTPUT_DIR, payload)
        logging.info("静的サイトを %s に生成しました。", OUTPUT_DIR)
        return 0
    except Exception as exc:
        logging.exception("サイト生成に失敗しました: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
