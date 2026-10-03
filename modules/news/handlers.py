from __future__ import annotations

import logging

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from modules.ai.handlers import split_message
from modules.news.digest import create_digest


logger = logging.getLogger(__name__)
router = Router(name="news")


@router.message(Command("news"))
async def handle_news(message: Message) -> None:
    """Create an on-demand digest and send it only to the requesting user."""
    progress = await message.answer(
        "Güncel haberler toplanıyor ve yerel AI ile özetleniyor...",
        parse_mode=None,
    )
    try:
        digest = await create_digest()
    except Exception:
        logger.exception("On-demand news digest failed")
        await progress.edit_text(
            "Haber özeti şu anda hazırlanamadı. İnternet veya yerel AI servisi "
            "geçici olarak kullanılamıyor olabilir.",
            parse_mode=None,
        )
        return

    chunks = iter(split_message(digest))
    await progress.edit_text(next(chunks), parse_mode="HTML")
    for chunk in chunks:
        await message.answer(chunk, parse_mode="HTML")
