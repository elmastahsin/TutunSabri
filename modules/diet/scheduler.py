"""Background loop that delivers meal, water, check-in and weigh-in reminders."""

from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from core.database import SessionFactory
from core.models import User
from modules.ai.handlers import split_message
from modules.diet import repository as repo
from modules.diet.config import (
    CHECKIN_TIME,
    DAILY_LIST_LEAD_MINUTES,
    EVENT_GRACE_MINUTES,
    MEAL_SLOTS,
    SCHEDULER_TICK_SECONDS,
    TIMEZONE,
    WATER_TIMES,
    WEIGH_IN_TIME,
    WEIGH_IN_WEEKDAY,
)
from modules.diet.llm import DietAIError
from modules.diet.models import DietProfile
from modules.diet.planner import (
    get_or_create_plan,
    meal_keyboard,
    render_daily_list,
    render_meal,
)


logger = logging.getLogger(__name__)
WATER_MESSAGES = (
    "💧 Su molası! Bir bardak su içmeye ne dersin?",
    "💧 Hatırlatma: Şu an bir bardak su tam zamanı.",
    "💧 Susamayı bekleme — bir bardak su iç, metabolizman teşekkür etsin.",
    "💧 Masandaki bardak boş mu? Doldurup bir bardak su iç.",
)


@dataclass(frozen=True)
class DueEvent:
    key: str
    kind: str
    at: datetime
    slot: str = ""


def _at(day: date, hhmm: str) -> datetime:
    hour, minute = (int(part) for part in hhmm.split(":"))
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=TIMEZONE)


def events_for_day(profile: DietProfile, day: date) -> Iterator[DueEvent]:
    stamp = day.isoformat()
    meal_times = repo.meal_times_of(profile)
    first_meal = min(_at(day, value) for value in meal_times.values())
    yield DueEvent(f"{stamp}:list", "list", first_meal - timedelta(minutes=DAILY_LIST_LEAD_MINUTES))
    for slot in MEAL_SLOTS:
        yield DueEvent(f"{stamp}:meal:{slot.key}", "meal", _at(day, meal_times[slot.key]), slot.key)
    if profile.water_enabled:
        for hhmm in WATER_TIMES:
            yield DueEvent(f"{stamp}:water:{hhmm}", "water", _at(day, hhmm))
    if profile.checkin_enabled:
        yield DueEvent(f"{stamp}:checkin", "checkin", _at(day, CHECKIN_TIME))
    if profile.weighin_enabled and day.weekday() == WEIGH_IN_WEEKDAY:
        yield DueEvent(f"{stamp}:weighin", "weighin", _at(day, WEIGH_IN_TIME))


def due_events(profile: DietProfile, now: datetime) -> list[DueEvent]:
    window_start = now - timedelta(minutes=EVENT_GRACE_MINUTES)
    return sorted(
        (event for event in events_for_day(profile, now.date()) if window_start <= event.at <= now),
        key=lambda event: event.at,
    )


async def send_daily_list(bot: Bot, profile: DietProfile, user: User, day: date) -> None:
    plan = await get_or_create_plan(profile, day)
    text = render_daily_list(plan, day, repo.meal_times_of(profile))
    for chunk in split_message(text):
        await bot.send_message(
            user.telegram_user_id, chunk, parse_mode="HTML", disable_web_page_preview=True
        )


async def send_meal(bot: Bot, profile: DietProfile, user: User, day: date, slot_key: str) -> None:
    plan = await get_or_create_plan(profile, day)
    meal = next(m for m in plan["meals"] if m["slot"] == slot_key)
    await bot.send_message(
        user.telegram_user_id,
        render_meal(meal, repo.meal_times_of(profile)[slot_key]),
        parse_mode="HTML",
        disable_web_page_preview=True,
        reply_markup=meal_keyboard(day, slot_key),
    )


def checkin_keyboard(day: date) -> InlineKeyboardMarkup:
    stamp = day.strftime("%Y%m%d")
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🟢 Tam uydum", callback_data=f"diet:check:{stamp}:full"),
                InlineKeyboardButton(text="🟡 Kısmen", callback_data=f"diet:check:{stamp}:partial"),
                InlineKeyboardButton(text="🔴 Uymadım", callback_data=f"diet:check:{stamp}:none"),
            ]
        ]
    )


WEIGHT_KEYBOARD = InlineKeyboardMarkup(
    inline_keyboard=[[InlineKeyboardButton(text="⚖️ Kilomu gir", callback_data="diet:weight")]]
)


async def _deliver(bot: Bot, profile: DietProfile, user: User, event: DueEvent) -> None:
    chat_id = user.telegram_user_id
    day = event.at.date()
    if event.kind == "list":
        await send_daily_list(bot, profile, user, day)
    elif event.kind == "meal":
        await send_meal(bot, profile, user, day, event.slot)
    elif event.kind == "water":
        await bot.send_message(chat_id, random.choice(WATER_MESSAGES), parse_mode=None)
    elif event.kind == "checkin":
        await bot.send_message(
            chat_id,
            "🌙 Günü kapatmadan önce: Bugün plana ne kadar uyabildin?\n"
            "Cevabın yarınki planı hazırlarken dikkate alınacak.",
            parse_mode=None,
            reply_markup=checkin_keyboard(day),
        )
    elif event.kind == "weighin":
        await bot.send_message(
            chat_id,
            "⚖️ Haftalık tartı zamanı! Sabah aç karnına tartılıp kilonu girer misin?\n"
            "Ya da doğrudan yaz: /kilo 78.4",
            parse_mode=None,
            reply_markup=WEIGHT_KEYBOARD,
        )


async def _tick(bot: Bot) -> None:
    now = datetime.now(TIMEZONE)
    async with SessionFactory() as session:
        targets = await repo.get_enabled_profiles(session)
    for profile, user in targets:
        for event in due_events(profile, now):
            async with SessionFactory() as session:
                # Claim first: a failing AI call must not be retried every tick.
                if not await repo.claim_event(session, profile.user_id, event.key):
                    continue
            try:
                await _deliver(bot, profile, user, event)
            except DietAIError:
                logger.warning("Diet plan unavailable for event %s", event.key, exc_info=True)
                await bot.send_message(
                    user.telegram_user_id,
                    "🩺 Bugünkü planını şu an hazırlayamadım (AI servisine ulaşılamadı). "
                    "Biraz sonra /diyet menüsünden tekrar deneyebilirsin.",
                    parse_mode=None,
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Diet notification failed event=%s user_id=%s", event.key, user.id)


async def run_diet_scheduler(bot: Bot) -> None:
    while True:
        try:
            await _tick(bot)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Diet scheduler tick failed")
        await asyncio.sleep(SCHEDULER_TICK_SECONDS)
