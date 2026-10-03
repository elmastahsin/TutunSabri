"""Database models for the dietitian module."""

from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from core.database import Base


class DietProfile(Base):
    __tablename__ = "diet_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), unique=True, index=True
    )
    sex: Mapped[str] = mapped_column(String(8))
    age: Mapped[int] = mapped_column(Integer)
    height_cm: Mapped[int] = mapped_column(Integer)
    weight_kg: Mapped[float] = mapped_column(Float)
    target_weight_kg: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    goal: Mapped[str] = mapped_column(String(16))
    activity: Mapped[str] = mapped_column(String(16))
    restrictions: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    preferences: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    meal_times: Mapped[str] = mapped_column(Text, default="{}")
    meal_pattern: Mapped[str] = mapped_column(String(8), default="5")
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    water_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    checkin_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    weighin_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class DietPlan(Base):
    __tablename__ = "diet_plans"
    __table_args__ = (UniqueConstraint("user_id", "plan_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    plan_date: Mapped[date] = mapped_column(Date, index=True)
    content: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class DietLog(Base):
    """Adherence and progress entries: eaten meals, daily check-ins, weights."""

    __tablename__ = "diet_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    log_date: Mapped[date] = mapped_column(Date, index=True)
    kind: Mapped[str] = mapped_column(String(16), index=True)
    slot: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    value: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class DietEvent(Base):
    """Notifications already delivered, so restarts never send duplicates."""

    __tablename__ = "diet_events"
    __table_args__ = (UniqueConstraint("user_id", "event_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    event_key: Mapped[str] = mapped_column(String(64))
    sent_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
