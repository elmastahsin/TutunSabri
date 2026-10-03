from __future__ import annotations

import asyncio
import logging
import re
import tempfile
from datetime import datetime
from pathlib import Path

import edge_tts
from aiogram import Router
from aiogram.filters import Command
from aiogram.types import FSInputFile, Message

from core.config import settings
from modules.ai.client import OllamaClient, OllamaError
from modules.news.digest import TIMEZONE, NewsItem, fetch_news


logger = logging.getLogger(__name__)
router = Router(name="podcast")
_generation_lock = asyncio.Lock()


def _podcast_prompt(items: list[NewsItem]) -> str:
    sources = "\n".join(
        f"- [{item.category}] {item.title} ({item.published})"
        for item in items
    )
    return f"""Aşağıdaki güncel haber başlıklarından Türkçe, tek sunuculu kısa bir haber podcasti metni yaz.

Kurallar:
- Doğal, sakin ve konuşma diline yakın bir anlatım kullan.
- Önce gündemi, sonra teknoloji, yazılım ve yapay zekâ gelişmelerini anlat.
- Yalnızca verilen başlıklardaki bilgileri kullan; ayrıntı veya alıntı uydurma.
- Her haberi kısa biçimde aktar ve bölümler arasında doğal geçişler kur.
- URL, kaynak numarası, Markdown, emoji, sahne yönergesi ve başlık kullanma.
- Kısaltmaları Türkçe okunabilecek şekilde aç.
- Yaklaşık 450-650 kelime yaz.
- Kısa bir selamlamayla başla ve kapanış cümlesiyle bitir.

Haberler:
{sources}"""


def _fallback_script(items: list[NewsItem]) -> str:
    lines = [
        "Merhaba. Tütün Sabri güncel haber özetine hoş geldiniz.",
        "Bugün öne çıkan başlıklar şöyle.",
    ]
    current_category = ""
    for item in items[:14]:
        if item.category != current_category:
            current_category = item.category
            lines.append(f"Şimdi {current_category} haberlerine bakalım.")
        lines.append(item.title.rstrip(". ") + ".")
    lines.append("Günün kısa haber özeti bu kadardı. Görüşmek üzere.")
    return "\n\n".join(lines)


def _clean_script(text: str) -> str:
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"[*_#`]+", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


async def create_podcast_script() -> str:
    items = await fetch_news()
    if not items:
        raise RuntimeError("Podcast için haber alınamadı.")
    try:
        script = await OllamaClient().chat(_podcast_prompt(items))
    except OllamaError:
        logger.warning("Ollama unavailable; using fallback podcast script", exc_info=True)
        script = _fallback_script(items)
    script = _clean_script(script)
    if not script:
        raise RuntimeError("Podcast metni oluşturulamadı.")
    return script


async def synthesize_podcast(script: str, output_path: Path) -> None:
    communicate = edge_tts.Communicate(
        script,
        settings.podcast_tts_voice,
        rate=settings.podcast_tts_rate,
    )
    await communicate.save(str(output_path))


@router.message(Command("podcast"))
async def handle_podcast(message: Message) -> None:
    if _generation_lock.locked():
        await message.answer(
            "Başka bir podcast hazırlanıyor. Birkaç dakika sonra tekrar deneyin."
        )
        return

    progress = await message.answer(
        "Güncel haber podcasti hazırlanıyor. Bu işlem birkaç dakika sürebilir...",
        parse_mode=None,
    )
    async with _generation_lock:
        try:
            script = await create_podcast_script()
            with tempfile.TemporaryDirectory(prefix="tutunsabri-podcast-") as temp_dir:
                date_label = datetime.now(TIMEZONE).strftime("%Y-%m-%d_%H-%M")
                mp3_path = Path(temp_dir) / f"tutunsabri_haber_{date_label}.mp3"
                await synthesize_podcast(script, mp3_path)
                await message.answer_audio(
                    FSInputFile(mp3_path),
                    title="Tütün Sabri Güncel Haber Podcasti",
                    performer="Tütün Sabri",
                    caption="Gündem, teknoloji ve yazılım haberlerinin AI özeti.",
                )
        except Exception:
            logger.exception("On-demand podcast generation failed")
            await progress.edit_text(
                "Podcast şu anda hazırlanamadı. İnternet, Ollama veya Edge TTS "
                "geçici olarak kullanılamıyor olabilir.",
                parse_mode=None,
            )
            return
    await progress.delete()
