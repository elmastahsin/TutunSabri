"""Persistence helpers for the dietitian module."""

from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Any, Optional

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from core.models import User
from modules.diet.config import MEAL_SLOTS
from modules.diet.models import DietEvent, DietLog, DietPlan, DietProfile


def meal_times_of(profile: DietProfile) -> dict[str, str]:
    try:
        stored = json.loads(profile.meal_times or "{}")
    except ValueError:
        stored = {}
    return {slot.key: stored.get(slot.key, slot.default_time) for slot in MEAL_SLOTS}


async def get_profile(session: AsyncSession, user_id: int) -> Optional[DietProfile]:
    result = await session.execute(
        select(DietProfile).where(DietProfile.user_id == user_id)
    )
    return result.scalars().first()


async def save_profile(
    session: AsyncSession, user_id: int, fields: dict[str, Any]
) -> DietProfile:
    profile = await get_profile(session, user_id)
    if profile is None:
        profile = DietProfile(user_id=user_id, meal_times="{}", is_enabled=True)
        session.add(profile)
    for name, value in fields.items():
        setattr(profile, name, value)
    await session.commit()
    await session.refresh(profile)
    return profile


async def get_enabled_profiles(
    session: AsyncSession,
) -> list[tuple[DietProfile, User]]:
    result = await session.execute(
        select(DietProfile, User)
        .join(User, User.id == DietProfile.user_id)
        .where(DietProfile.is_enabled.is_(True))
        .where(User.is_active.is_(True))
    )
    return [(profile, user) for profile, user in result.all()]


async def get_plan(
    session: AsyncSession, user_id: int, plan_date: date
) -> Optional[DietPlan]:
    result = await session.execute(
        select(DietPlan)
        .where(DietPlan.user_id == user_id)
        .where(DietPlan.plan_date == plan_date)
    )
    return result.scalars().first()


async def save_plan(
    session: AsyncSession,
    user_id: int,
    plan_date: date,
    content: dict[str, Any],
    source: str,
) -> DietPlan:
    plan = await get_plan(session, user_id, plan_date)
    encoded = json.dumps(content, ensure_ascii=False)
    if plan is None:
        plan = DietPlan(user_id=user_id, plan_date=plan_date, content=encoded, source=source)
        session.add(plan)
    else:
        plan.content = encoded
        plan.source = source
    await session.commit()
    return plan


async def get_recent_meal_titles(
    session: AsyncSession, user_id: int, before: date, days: int
) -> list[str]:
    result = await session.execute(
        select(DietPlan.content)
        .where(DietPlan.user_id == user_id)
        .where(DietPlan.plan_date < before)
        .where(DietPlan.plan_date >= before - timedelta(days=days))
    )
    titles: list[str] = []
    for content in result.scalars().all():
        try:
            meals = json.loads(content).get("meals", [])
        except (ValueError, AttributeError):
            continue
        titles.extend(str(meal.get("title", "")) for meal in meals if meal.get("title"))
    return titles


async def add_log(
    session: AsyncSession,
    user_id: int,
    log_date: date,
    kind: str,
    value: str,
    slot: Optional[str] = None,
) -> None:
    if kind != "weight":
        # Meal and check-in answers are idempotent per day: keep the latest.
        query = (
            delete(DietLog)
            .where(DietLog.user_id == user_id)
            .where(DietLog.log_date == log_date)
            .where(DietLog.kind == kind)
        )
        if slot is not None:
            query = query.where(DietLog.slot == slot)
        await session.execute(query)
    session.add(DietLog(user_id=user_id, log_date=log_date, kind=kind, slot=slot, value=value))
    await session.commit()


async def get_logs_since(
    session: AsyncSession, user_id: int, since: date
) -> list[DietLog]:
    result = await session.execute(
        select(DietLog)
        .where(DietLog.user_id == user_id)
        .where(DietLog.log_date >= since)
        .order_by(DietLog.log_date.asc(), DietLog.id.asc())
    )
    return list(result.scalars().all())


async def get_weight_history(
    session: AsyncSession, user_id: int, limit: int = 8
) -> list[DietLog]:
    result = await session.execute(
        select(DietLog)
        .where(DietLog.user_id == user_id)
        .where(DietLog.kind == "weight")
        .order_by(DietLog.log_date.desc(), DietLog.id.desc())
        .limit(limit)
    )
    return list(reversed(result.scalars().all()))


async def claim_event(session: AsyncSession, user_id: int, event_key: str) -> bool:
    """Record a notification as sent; return False if it was already claimed."""
    session.add(DietEvent(user_id=user_id, event_key=event_key))
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        return False
    return True
