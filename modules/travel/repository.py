"""Persistence helpers for the travel guide module."""

from __future__ import annotations

import json
from datetime import date
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from core.models import User
from modules.travel.models import Trip, TripEvent


async def create_trip(session: AsyncSession, **fields: Any) -> Trip:
    trip = Trip(**fields)
    session.add(trip)
    await session.commit()
    await session.refresh(trip)
    return trip


async def get_user_trip(session: AsyncSession, user_id: int, trip_id: int) -> Optional[Trip]:
    result = await session.execute(select(Trip).where(Trip.id == trip_id).where(Trip.user_id == user_id))
    return result.scalars().first()


async def get_upcoming_trips(session: AsyncSession, user_id: int, today: date) -> list[Trip]:
    result = await session.execute(
        select(Trip)
        .where(Trip.user_id == user_id)
        .where(Trip.is_active.is_(True))
        .where(Trip.end_date >= today)
        .order_by(Trip.start_date.asc())
    )
    return list(result.scalars().all())


async def get_schedulable_trips(session: AsyncSession, today: date) -> list[tuple[Trip, User]]:
    """Active trips that have not ended yet (a day of slack covers time zones)."""
    result = await session.execute(
        select(Trip, User)
        .join(User, User.id == Trip.user_id)
        .where(Trip.is_active.is_(True))
        .where(Trip.plan.is_not(None))
        .where(Trip.end_date >= date.fromordinal(today.toordinal() - 1))
        .where(User.is_active.is_(True))
    )
    return [(trip, user) for trip, user in result.all()]


async def save_trip_plan(session: AsyncSession, trip_id: int, plan: dict[str, Any]) -> None:
    trip = await session.get(Trip, trip_id)
    if trip is not None:
        trip.plan = json.dumps(plan, ensure_ascii=False)
        await session.commit()


async def deactivate_trip(session: AsyncSession, trip: Trip) -> None:
    trip.is_active = False
    await session.commit()


async def claim_event(session: AsyncSession, trip_id: int, event_key: str) -> bool:
    session.add(TripEvent(trip_id=trip_id, event_key=event_key))
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        return False
    return True
