"""Background loop for packing reminders and daily trip briefings."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from aiogram import Bot

from core.database import SessionFactory
from core.models import User
from modules.ai.handlers import split_message
from modules.travel import repository as repo
from modules.travel.config import (
    EVENT_GRACE_MINUTES,
    MORNING_BRIEF_TIME,
    PACKING_REMINDER_TIME,
    SCHEDULER_TICK_SECONDS,
)
from modules.travel.models import Trip
from modules.travel.planner import plan_of, render_day, render_packing, trip_days
from modules.travel.weather import forecast


logger = logging.getLogger(__name__)


def _local(trip: Trip, day, hhmm: str) -> datetime:
    hour, minute = (int(part) for part in hhmm.split(":"))
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=ZoneInfo(trip.timezone))


def due_events(trip: Trip, now: datetime) -> list[tuple[str, int]]:
    """(event_key, day_index) pairs due now; day_index -1 is the packing reminder."""
    candidates = [("pack", -1, _local(trip, trip.start_date - timedelta(days=1), PACKING_REMINDER_TIME))]
    candidates.extend(
        (f"day{index + 1}", index, _local(trip, day, MORNING_BRIEF_TIME))
        for index, day in enumerate(trip_days(trip))
    )
    window_start = now - timedelta(minutes=EVENT_GRACE_MINUTES)
    return [(key, index) for key, index, at in candidates if window_start <= at <= now]


async def _deliver(bot: Bot, trip: Trip, user: User, day_index: int) -> None:
    plan = plan_of(trip)
    today = datetime.now(ZoneInfo(trip.timezone)).date()
    weather = await forecast(trip.latitude, trip.longitude, trip.start_date, trip.end_date, today)
    if day_index < 0:
        text = "✈️ Yarın yola çıkıyorsun!\n\n" + render_packing(trip, plan, weather)
    else:
        text = render_day(trip, plan, day_index, weather.get(trip_days(trip)[day_index]), greeting=True)
    for chunk in split_message(text):
        await bot.send_message(user.telegram_user_id, chunk, parse_mode="HTML", disable_web_page_preview=True)


async def _tick(bot: Bot) -> None:
    now = datetime.now(ZoneInfo("UTC"))
    async with SessionFactory() as session:
        trips = await repo.get_schedulable_trips(session, now.date())
    for trip, user in trips:
        for key, day_index in due_events(trip, now):
            async with SessionFactory() as session:
                if not await repo.claim_event(session, trip.id, key):
                    continue
            try:
                await _deliver(bot, trip, user, day_index)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Trip notification failed trip_id=%s event=%s", trip.id, key)


async def run_travel_scheduler(bot: Bot) -> None:
    while True:
        try:
            await _tick(bot)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Travel scheduler tick failed")
        await asyncio.sleep(SCHEDULER_TICK_SECONDS)
