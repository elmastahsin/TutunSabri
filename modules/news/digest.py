from __future__ import annotations

import asyncio
import html
import logging
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import quote_plus
from zoneinfo import ZoneInfo

import httpx
from aiogram import Bot

from core.database import SessionFactory
from core.repositories import get_admin_users
from modules.ai.client import OllamaClient, OllamaError
from modules.ai.handlers import split_message


logger = logging.getLogger(__name__)
TIMEZONE = ZoneInfo("Europe/Istanbul")
SEND_TIMES = (time(hour=9, minute=0), time(hour=19, minute=0))
STATE_FILE = Path("data/news_last_sent_date.txt")
REQUEST_TIMEOUT = 25.0
MAX_ITEMS_PER_FEED = 6


def _google_news_url(query: str | None = None) -> str:
    base = "https://news.google.com/rss"
    if query:
        return f"{base}/search?q={quote_plus(query)}&hl=tr&gl=TR&ceid=TR:tr"
    return f"{base}?hl=tr&gl=TR&ceid=TR:tr"


FEEDS = (
    ("Gündem", _google_news_url()),
    ("Teknoloji", _google_news_url("teknoloji when:1d")),
    ("Yazılım", _google_news_url("yazılım OR programlama OR yapay zeka when:1d")),
)


@dataclass(frozen=True)
class NewsItem:
    category: str
    title: str
    link: str
    published: str


def _clean_text(value: str) -> str:
    value = html.unescape(re.sub(r"<[^>]+>", " ", value or ""))
    return re.sub(r"\s+", " ", value).strip()


def _parse_feed(category: str, xml_text: str) -> list[NewsItem]:
    root = ET.fromstring(xml_text)
    items: list[NewsItem] = []
    for node in root.findall(".//item")[:MAX_ITEMS_PER_FEED]:
        title = _clean_text(node.findtext("title", ""))
        link = (node.findtext("link", "") or "").strip()
        raw_date = (node.findtext("pubDate", "") or "").strip()
        try:
            published = parsedate_to_datetime(raw_date).astimezone(TIMEZONE).strftime(
                "%d.%m %H:%M"
            )
        except (TypeError, ValueError, OverflowError):
            published = raw_date
        if title and link:
            items.append(NewsItem(category, title, link, published))
    return items


async def fetch_news() -> list[NewsItem]:
    async with httpx.AsyncClient(
        timeout=REQUEST_TIMEOUT,
        follow_redirects=True,
        headers={"User-Agent": "TutunSabri/1.0 daily-news-digest"},
    ) as client:
        responses = await asyncio.gather(
            *(client.get(url) for _, url in FEEDS), return_exceptions=True
        )

    news: list[NewsItem] = []
    seen_titles: set[str] = set()
    for (category, _), response in zip(FEEDS, responses):
        if isinstance(response, Exception):
            logger.warning("News feed could not be fetched: %s", category, exc_info=response)
            continue
        try:
            response.raise_for_status()
            parsed_items = _parse_feed(category, response.text)
        except (httpx.HTTPError, ET.ParseError):
            logger.warning("News feed could not be parsed: %s", category, exc_info=True)
            continue
        for item in parsed_items:
            normalized = item.title.casefold()
            if normalized not in seen_titles:
                seen_titles.add(normalized)
                news.append(item)
    return news


def _period_labels(scheduled_for: datetime) -> tuple[str, str]:
    if scheduled_for.hour < 12:
        return "sabah", "Günaydın — Günün Haber Özeti"
    return "akşam", "İyi akşamlar — Akşam Haber Özeti"


def _build_prompt(items: list[NewsItem], scheduled_for: datetime) -> str:
    period, title = _period_labels(scheduled_for)
    source_lines = "\n".join(
        f"[{index}] [{item.category}] {item.title} ({item.published}) | {item.link}"
        for index, item in enumerate(items, start=1)
    )
    return f"""Aşağıdaki güncel haber listesinden Türkçe bir {period} bülteni hazırla.

Kurallar:
- Önce '🗞 Gündem', ardından '💻 Teknoloji ve Yazılım' başlıklarını kullan.
- Her bölümde en önemli 3-5 maddeyi seç.
- Her madde en fazla iki kısa cümle olsun; paragraflar arasında boş satır bırak.
- Yalnızca verilen başlıklardaki bilgileri kullan; ayrıntı uydurma.
- Her maddenin sonuna kaynak numarasını tam olarak [1] biçiminde ekle.
- Uzun internet adreslerini çıktıya yazma; yalnızca [numara] kaynak gösterimini kullan.
- Markdown işaretleri kullanma; sade metin ve madde işaretleri kullan.
- Benzer haberleri birleştir. Toplam çıktı 3000 karakteri aşmasın.
- Başlık: '{title}'.

Kaynak listesi:
{source_lines}"""


def _fallback_digest(items: list[NewsItem], scheduled_for: datetime) -> str:
    _, title = _period_labels(scheduled_for)
    lines = [title, ""]
    current_category = ""
    for index, item in enumerate(items[:14], start=1):
        if item.category != current_category:
            current_category = item.category
            lines.extend((current_category, ""))
        lines.append(f"• {item.title} [{index}]")
    return "\n".join(lines)


def _render_digest_html(text: str, items: list[NewsItem]) -> str:
    """Escape model output and turn source markers into compact Telegram links."""
    rendered = html.escape(text)
    for index, item in reversed(list(enumerate(items, start=1))):
        link = html.escape(item.link, quote=True)
        anchor = f'<a href="{link}">Kaynak {index} ↗</a>'
        rendered = rendered.replace(html.escape(item.link), anchor)
        rendered = rendered.replace(f"[{index}]", anchor)
    return rendered


async def create_digest(scheduled_for: datetime | None = None) -> str:
    scheduled_for = scheduled_for or datetime.now(TIMEZONE)
    items = await fetch_news()
    if not items:
        raise RuntimeError("Hiçbir haber kaynağından veri alınamadı.")
    try:
        digest = await OllamaClient().chat(_build_prompt(items, scheduled_for))
    except OllamaError:
        logger.warning("Ollama unavailable; sending headline-only digest", exc_info=True)
        digest = _fallback_digest(items, scheduled_for)
    return _render_digest_html(digest, items)


async def send_daily_digest(bot: Bot, scheduled_for: datetime | None = None) -> bool:
    scheduled_for = scheduled_for or datetime.now(TIMEZONE)
    digest = await create_digest(scheduled_for)
    async with SessionFactory() as session:
        recipients = await get_admin_users(session)
    if not recipients:
        logger.warning("Daily news digest has no active admin recipient")
        return False
    for recipient in recipients:
        for chunk in split_message(digest):
            await bot.send_message(recipient.telegram_user_id, chunk, parse_mode="HTML")
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(_slot_key(scheduled_for), encoding="utf-8")
    return True


def _slot_key(scheduled_for: datetime) -> str:
    return scheduled_for.strftime("%Y-%m-%d@%H:%M")


def _already_sent(scheduled_for: datetime) -> bool:
    try:
        return STATE_FILE.read_text(encoding="utf-8").strip() == _slot_key(scheduled_for)
    except FileNotFoundError:
        return False


def next_run(now: datetime) -> datetime:
    for send_time in SEND_TIMES:
        target = datetime.combine(now.date(), send_time, tzinfo=TIMEZONE)
        if target > now:
            return target
    return datetime.combine(now.date() + timedelta(days=1), SEND_TIMES[0], tzinfo=TIMEZONE)


def seconds_until_next_run(now: datetime) -> float:
    return max(1.0, (next_run(now) - now).total_seconds())


async def run_daily_news_scheduler(bot: Bot) -> None:
    while True:
        scheduled_for = next_run(datetime.now(TIMEZONE))
        await asyncio.sleep(max(1.0, (scheduled_for - datetime.now(TIMEZONE)).total_seconds()))
        if _already_sent(scheduled_for):
            continue
        try:
            await send_daily_digest(bot, scheduled_for)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Daily news digest failed")
