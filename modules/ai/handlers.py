"""Telegram handlers for the local Ollama integration."""

from __future__ import annotations

import logging
from collections.abc import Iterator

from aiogram import Router
from aiogram.filters import Command
from aiogram.filters.command import CommandObject
from aiogram.types import Message

from modules.ai.client import (
    OllamaClient,
    OllamaConnectionError,
    OllamaError,
    OllamaTimeoutError,
)


logger = logging.getLogger(__name__)
router = Router(name="ai")
TELEGRAM_CHUNK_SIZE = 4000


def split_message(text: str, limit: int = TELEGRAM_CHUNK_SIZE) -> Iterator[str]:
    """Split text into Telegram-safe chunks, preferring line boundaries."""
    remaining = text.strip()
    while remaining:
        if len(remaining) <= limit:
            yield remaining
            return

        split_at = remaining.rfind("\n", 0, limit + 1)
        if split_at <= 0:
            split_at = remaining.rfind(" ", 0, limit + 1)
        if split_at <= 0:
            split_at = limit

        yield remaining[:split_at].rstrip()
        remaining = remaining[split_at:].lstrip()


@router.message(Command("ai"))
async def handle_ai(message: Message, command: CommandObject) -> None:
    """Send the command prompt to Ollama and return its response."""
    prompt = (command.args or "").strip()
    if not prompt:
        await message.answer(
            "Kullanım: `/ai sorunuzu buraya yazın`\nÖrnek: `/ai Merhaba`"
        )
        return

    thinking_message = await message.answer("Düşünüyorum...", parse_mode=None)
    try:
        response_text = await OllamaClient().chat(prompt)
    except OllamaTimeoutError:
        logger.warning("Ollama response timed out", exc_info=True)
        await thinking_message.edit_text(
            "Model yanıtı zamanında tamamlayamadı. Lütfen sorunuzu kısaltıp "
            "tekrar deneyin."
        )
        return
    except OllamaConnectionError:
        logger.warning("Local Ollama service is unavailable", exc_info=True)
        await thinking_message.edit_text(
            "Ollama servisine bağlanılamadı. Servisin çalıştığını kontrol edin."
        )
        return
    except OllamaError:
        logger.exception("Ollama chat request failed")
        await thinking_message.edit_text(
            "AI yanıtı alınırken bir hata oluştu. Lütfen daha sonra tekrar deneyin."
        )
        return

    chunks = iter(split_message(response_text))
    await thinking_message.edit_text(next(chunks), parse_mode=None)
    for chunk in chunks:
        await message.answer(chunk, parse_mode=None)
