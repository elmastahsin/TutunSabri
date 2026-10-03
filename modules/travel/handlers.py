"""Telegram handlers for the travel guide: trip wizard, trip list and plan views."""

from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta
from typing import Optional

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from core.database import SessionFactory
from core.models import User
from modules.ai.handlers import split_message
from modules.ai.llm import LLMUnavailableError
from modules.diet.config import TIMEZONE
from modules.travel import repository as repo
from modules.travel.config import BUDGETS, COMPANIONS, INTERESTS, MAX_TRIP_DAYS
from modules.travel.models import Trip
from modules.travel.planner import (
    build_plan,
    plan_of,
    render_day,
    render_overview,
    render_packing,
    resolve_destination,
    trip_days,
)
from modules.travel.weather import forecast


logger = logging.getLogger(__name__)
router = Router(name="travel")
DATE_PART = r"(\d{1,2})(?:[./](\d{1,2})(?:[./](\d{2,4}))?)?"
DATE_PATTERN = re.compile(rf"^{DATE_PART}$")
RANGE_PATTERN = re.compile(rf"^{DATE_PART}\s*(?:-|–|—|ile)\s*{DATE_PART}$")
MONTHS = {
    "ocak": 1, "şubat": 2, "subat": 2, "mart": 3, "nisan": 4, "mayıs": 5, "mayis": 5,
    "haziran": 6, "temmuz": 7, "ağustos": 8, "agustos": 8, "eylül": 9, "eylul": 9,
    "ekim": 10, "kasım": 11, "kasim": 11, "aralık": 12, "aralik": 12,
}
MONTH_NAME_PATTERN = re.compile(r"\s*\b(" + "|".join(MONTHS) + r")\b")


class TripSetup(StatesGroup):
    destination = State()
    confirm_place = State()
    start_date = State()
    days = State()
    companions = State()
    interests = State()
    budget = State()
    notes = State()


def _today() -> date:
    return datetime.now(TIMEZONE).date()


def _buttons(prefix: str, options: dict[str, str], per_row: int = 1) -> InlineKeyboardMarkup:
    buttons = [InlineKeyboardButton(text=label, callback_data=f"tset:{prefix}:{key}") for key, label in options.items()]
    return InlineKeyboardMarkup(inline_keyboard=[buttons[i : i + per_row] for i in range(0, len(buttons), per_row)])


def _interests_keyboard(selected: list[str]) -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(text=("✅ " if key in selected else "") + label, callback_data=f"tset:interest:{key}")
        for key, label in INTERESTS.items()
    ]
    rows = [buttons[i : i + 2] for i in range(0, len(buttons), 2)]
    rows.append([InlineKeyboardButton(text="Devam ➡️", callback_data="tset:interest:done")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _trip_keyboard(trip: Trip) -> InlineKeyboardMarkup:
    day_buttons = [
        InlineKeyboardButton(text=f"{index + 1}. gün", callback_data=f"trip:day:{trip.id}:{index}")
        for index in range(len(trip_days(trip)))
    ]
    rows = [day_buttons[i : i + 4] for i in range(0, len(day_buttons), 4)]
    rows.append([
        InlineKeyboardButton(text="🎒 Bavul listesi", callback_data=f"trip:pack:{trip.id}"),
        InlineKeyboardButton(text="🔄 Yeniden planla", callback_data=f"trip:regen:{trip.id}"),
    ])
    rows.append([InlineKeyboardButton(text="🗑 Geziyi sil", callback_data=f"trip:cancel:{trip.id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _make_date(year: int, month: int, day: int) -> Optional[date]:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _full_year(raw: Optional[str]) -> Optional[int]:
    if raw is None:
        return None
    return int(raw) + (2000 if len(raw) == 2 else 0)


def _parse_dates(text: str, today: date) -> Optional[tuple[date, Optional[date]]]:
    """Parse a start date or a date range; returns (start, end or None).

    Accepts "15.10", "15.10.2026", "yarın", "15.10 - 18.10", "15-18.10",
    "15-18 Ekim" and "28 Aralık - 3 Ocak". Missing years resolve to the
    next occurrence; a missing start month is taken from the end date.
    """
    text = text.strip().casefold()
    if text == "bugün":
        return today, None
    if text == "yarın":
        return today + timedelta(days=1), None
    text = MONTH_NAME_PATTERN.sub(lambda m: f".{MONTHS[m.group(1)]}", text)

    single = DATE_PATTERN.match(text)
    if single and single.group(2):
        day, month, year = int(single.group(1)), int(single.group(2)), _full_year(single.group(3))
        start = _make_date(year or today.year, month, day)
        if start is not None and year is None and start < today:
            start = _make_date(today.year + 1, month, day)
        return (start, None) if start else None

    ranged = RANGE_PATTERN.match(text)
    if not ranged or not ranged.group(5):
        return None
    d1, m1, y1 = int(ranged.group(1)), ranged.group(2), _full_year(ranged.group(3))
    d2, m2, y2 = int(ranged.group(4)), int(ranged.group(5)), _full_year(ranged.group(6))
    m1 = int(m1) if m1 else m2
    if y1 is None and y2 is None:
        start = _make_date(today.year, m1, d1)
        if start is not None and start < today:
            start = _make_date(today.year + 1, m1, d1)
    else:
        start = _make_date(y1 or y2, m1, d1)
        if start is not None and y1 is None and (m1, d1) > (m2, d2):
            start = _make_date(y2 - 1, m1, d1)
    if start is None:
        return None
    end = _make_date(y2 or start.year, m2, d2)
    if end is not None and y2 is None and end < start:
        end = _make_date(start.year + 1, m2, d2)
    return (start, end) if end else None


# --- Entry points ----------------------------------------------------------


@router.message(Command("gezi"))
async def handle_trips(message: Message, state: FSMContext, db_user: User) -> None:
    await state.clear()
    async with SessionFactory() as session:
        trips = await repo.get_upcoming_trips(session, db_user.id, _today())
    rows = [
        [InlineKeyboardButton(
            text=f"🧳 {trip.destination} · {trip.start_date:%d.%m}–{trip.end_date:%d.%m}",
            callback_data=f"trip:open:{trip.id}",
        )]
        for trip in trips
    ]
    rows.append([InlineKeyboardButton(text="➕ Yeni gezi planla", callback_data="trip:new")])
    text = "🗺 <b>Seyahat rehberin</b>\n\n" + (
        "Yaklaşan gezilerin aşağıda." if trips else "Henüz planlanmış bir gezin yok. Hadi bir tane planlayalım!"
    )
    await message.answer(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@router.callback_query(F.data == "trip:new")
async def cb_new_trip(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await state.clear()
    await state.set_state(TripSetup.destination)
    await callback.message.answer(
        "1/6 · Nereye gidiyorsun? Şehir, bölge veya ülke yazabilirsin (ör. Roma, Kapadokya, Batum).",
        parse_mode=None,
    )


# --- Wizard ----------------------------------------------------------------


@router.message(TripSetup.destination)
async def setup_destination(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()[:100]
    if not text:
        return
    progress = await message.answer("📍 Konumu buluyorum...", parse_mode=None)
    try:
        place = await resolve_destination(text)
    except Exception:
        logger.warning("Destination lookup failed", exc_info=True)
        place = None
    if place is None:
        await progress.edit_text("Bu yeri bulamadım. Yakındaki bir şehrin adını yazar mısın?", parse_mode=None)
        return
    await state.update_data(
        destination=text[:1].upper() + text[1:], country=place.country, country_code=place.country_code,
        latitude=place.latitude, longitude=place.longitude, timezone=place.timezone,
    )
    await state.set_state(TripSetup.confirm_place)
    await progress.edit_text(
        f"📍 {place.label} — doğru mu?",
        parse_mode=None,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="✅ Evet", callback_data="tset:place:yes"),
            InlineKeyboardButton(text="✏️ Tekrar yaz", callback_data="tset:place:no"),
        ]]),
    )


@router.callback_query(TripSetup.confirm_place, F.data.startswith("tset:place:"))
async def setup_confirm_place(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    if callback.data.endswith(":no"):
        await state.set_state(TripSetup.destination)
        await callback.message.edit_text("Tamam, şehir adını ülkesiyle birlikte yazar mısın? (ör. Batum Gürcistan)", parse_mode=None)
        return
    await state.set_state(TripSetup.start_date)
    await callback.message.edit_text(
        "2/6 · Hangi tarihler arasında?\n"
        "Aralık yazabilirsin (ör. 15-18 Ekim, 15.10 - 18.10.2026) "
        "ya da sadece başlangıç tarihini (ör. 15.10, yarın).",
        parse_mode=None,
    )


@router.message(TripSetup.start_date)
async def setup_start_date(message: Message, state: FSMContext) -> None:
    parsed = _parse_dates(message.text or "", _today())
    if parsed is None or parsed[0] < _today():
        await message.answer(
            "Tarihi anlayamadım. Örnekler: 15-18 Ekim, 15.10 - 18.10, 15.10.2026", parse_mode=None
        )
        return
    start, end = parsed
    if end is not None:
        days = (end - start).days + 1
        if not 1 <= days <= MAX_TRIP_DAYS:
            await message.answer(
                f"Gezi en fazla {MAX_TRIP_DAYS} gün olabilir ve bitiş başlangıçtan önce olamaz. "
                "Tarihleri tekrar yazar mısın?",
                parse_mode=None,
            )
            return
        await state.update_data(start_date=start.isoformat())
        await message.answer(
            f"🗓 {start:%d.%m.%Y} – {end:%d.%m.%Y} ({days} gün)", parse_mode=None
        )
        await _set_days(message, state, days)
        return
    await state.update_data(start_date=start.isoformat())
    await state.set_state(TripSetup.days)
    await message.answer(
        f"{start:%d.%m.%Y} başlangıçlı. Kaç gün kalacaksın? (1-{MAX_TRIP_DAYS})",
        parse_mode=None,
        reply_markup=_buttons("days", {str(n): str(n) for n in range(1, 8)}, per_row=7),
    )


async def _set_days(message: Message, state: FSMContext, days: int) -> None:
    await state.update_data(days=days)
    await state.set_state(TripSetup.companions)
    await message.answer("3/6 · Kiminle gidiyorsun?", parse_mode=None, reply_markup=_buttons("comp", COMPANIONS, per_row=2))


@router.callback_query(TripSetup.days, F.data.startswith("tset:days:"))
async def setup_days_button(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await callback.message.edit_reply_markup(reply_markup=None)
    await _set_days(callback.message, state, int(callback.data.split(":")[2]))


@router.message(TripSetup.days)
async def setup_days_text(message: Message, state: FSMContext) -> None:
    try:
        days = int((message.text or "").strip())
    except ValueError:
        days = 0
    if not 1 <= days <= MAX_TRIP_DAYS:
        await message.answer(f"Lütfen 1 ile {MAX_TRIP_DAYS} arasında bir sayı yaz.", parse_mode=None)
        return
    await _set_days(message, state, days)


@router.callback_query(TripSetup.companions, F.data.startswith("tset:comp:"))
async def setup_companions(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await state.update_data(companions=callback.data.split(":")[2], interests=[])
    await state.set_state(TripSetup.interests)
    await callback.message.edit_text(
        "4/6 · Neler ilgini çeker? Birden fazla seçebilirsin.", parse_mode=None, reply_markup=_interests_keyboard([])
    )


@router.callback_query(TripSetup.interests, F.data.startswith("tset:interest:"))
async def setup_interests(callback: CallbackQuery, state: FSMContext) -> None:
    key = callback.data.split(":")[2]
    selected: list[str] = (await state.get_data()).get("interests", [])
    if key == "done":
        if not selected:
            await callback.answer("En az bir ilgi alanı seç.", show_alert=True)
            return
        await callback.answer()
        await state.set_state(TripSetup.budget)
        await callback.message.edit_text("5/6 · Bütçe seviyen?", parse_mode=None, reply_markup=_buttons("budget", BUDGETS, per_row=3))
        return
    if key in INTERESTS:
        selected = [k for k in selected if k != key] if key in selected else selected + [key]
        await state.update_data(interests=selected)
        await callback.message.edit_reply_markup(reply_markup=_interests_keyboard(selected))
    await callback.answer()


@router.callback_query(TripSetup.budget, F.data.startswith("tset:budget:"))
async def setup_budget(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await state.update_data(budget=callback.data.split(":")[2])
    await state.set_state(TripSetup.notes)
    await callback.message.edit_text(
        "6/6 · Eklemek istediğin bir şey var mı?\n(ör. araba kiralayacağım, müzeleri çok sevmem, otelim Taksim'de)",
        parse_mode=None,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Yok, planla ✨", callback_data="tset:notes:skip")]]),
    )


@router.message(TripSetup.notes)
async def setup_notes(message: Message, state: FSMContext, db_user: User) -> None:
    await state.update_data(notes=(message.text or "").strip()[:500] or None)
    await _create_trip(message, state, db_user)


@router.callback_query(TripSetup.notes, F.data == "tset:notes:skip")
async def setup_notes_skip(callback: CallbackQuery, state: FSMContext, db_user: User) -> None:
    await callback.answer()
    await callback.message.edit_reply_markup(reply_markup=None)
    await state.update_data(notes=None)
    await _create_trip(callback.message, state, db_user)


async def _create_trip(message: Message, state: FSMContext, db_user: User) -> None:
    data = await state.get_data()
    await state.clear()
    start = date.fromisoformat(data["start_date"])
    async with SessionFactory() as session:
        trip = await repo.create_trip(
            session,
            user_id=db_user.id,
            destination=data["destination"],
            country=data.get("country"),
            country_code=data.get("country_code"),
            latitude=data["latitude"],
            longitude=data["longitude"],
            timezone=data["timezone"],
            start_date=start,
            end_date=start + timedelta(days=data["days"] - 1),
            companions=data["companions"],
            interests=",".join(data["interests"]),
            budget=data["budget"],
            notes=data.get("notes"),
            is_active=True,
        )
    await _generate_and_show(message, trip)


async def _generate_and_show(message: Message, trip: Trip) -> None:
    progress = await message.answer("🗺 Rotanı hazırlıyorum, bu birkaç saniye sürebilir...", parse_mode=None)
    try:
        await build_plan(trip, _today())
    except LLMUnavailableError:
        await progress.edit_text(
            "Planı şu an hazırlayamadım (AI servisine ulaşılamadı). /gezi menüsünden geziyi açıp "
            "🔄 Yeniden planla ile tekrar deneyebilirsin.",
            parse_mode=None,
        )
        return
    await progress.delete()
    await _send_overview(message, trip)
    await message.answer(
        "🔔 Yola çıkmadan önceki akşam bavul listesi ve hava durumunu, gezi boyunca her sabah "
        "08:00'de (yerel saat) o günün rotasını göndereceğim.",
        parse_mode=None,
    )


async def _send_overview(message: Message, trip: Trip) -> None:
    weather = await forecast(trip.latitude, trip.longitude, trip.start_date, trip.end_date, _today())
    chunks = list(split_message(render_overview(trip, plan_of(trip), weather)))
    for index, chunk in enumerate(chunks):
        await message.answer(
            chunk, parse_mode="HTML", disable_web_page_preview=True,
            reply_markup=_trip_keyboard(trip) if index == len(chunks) - 1 else None,
        )


# --- Trip views ------------------------------------------------------------


async def _load_trip(callback: CallbackQuery, db_user: User, trip_id: str) -> Optional[Trip]:
    async with SessionFactory() as session:
        trip = await repo.get_user_trip(session, db_user.id, int(trip_id))
    if trip is None or not trip.is_active:
        await callback.answer("Bu gezi bulunamadı.", show_alert=True)
        return None
    return trip


@router.callback_query(F.data.regexp(r"^trip:open:\d+$"))
async def cb_open(callback: CallbackQuery, db_user: User) -> None:
    trip = await _load_trip(callback, db_user, callback.data.split(":")[2])
    if trip is None:
        return
    await callback.answer()
    if not trip.plan:
        await _generate_and_show(callback.message, trip)
        return
    await _send_overview(callback.message, trip)


@router.callback_query(F.data.regexp(r"^trip:day:\d+:\d+$"))
async def cb_day(callback: CallbackQuery, db_user: User) -> None:
    _, _, trip_id, index = callback.data.split(":")
    trip = await _load_trip(callback, db_user, trip_id)
    if trip is None or not trip.plan:
        return
    await callback.answer()
    index = int(index)
    day_date = trip_days(trip)[index]
    weather = await forecast(trip.latitude, trip.longitude, day_date, day_date, _today())
    for chunk in split_message(render_day(trip, plan_of(trip), index, weather.get(day_date))):
        await callback.message.answer(chunk, parse_mode="HTML", disable_web_page_preview=True)


@router.callback_query(F.data.regexp(r"^trip:pack:\d+$"))
async def cb_pack(callback: CallbackQuery, db_user: User) -> None:
    trip = await _load_trip(callback, db_user, callback.data.split(":")[2])
    if trip is None or not trip.plan:
        return
    await callback.answer()
    weather = await forecast(trip.latitude, trip.longitude, trip.start_date, trip.end_date, _today())
    await callback.message.answer(render_packing(trip, plan_of(trip), weather), parse_mode="HTML")


@router.callback_query(F.data.regexp(r"^trip:regen:\d+$"))
async def cb_regen(callback: CallbackQuery, db_user: User) -> None:
    trip = await _load_trip(callback, db_user, callback.data.split(":")[2])
    if trip is None:
        return
    await callback.answer("Yeniden planlanıyor...")
    await _generate_and_show(callback.message, trip)


@router.callback_query(F.data.regexp(r"^trip:cancel:\d+(:yes)?$"))
async def cb_cancel(callback: CallbackQuery, db_user: User) -> None:
    parts = callback.data.split(":")
    trip = await _load_trip(callback, db_user, parts[2])
    if trip is None:
        return
    if len(parts) == 3:
        await callback.answer()
        await callback.message.answer(
            f"{trip.destination} gezisini silmek istediğine emin misin? Bildirimler de duracak.",
            parse_mode=None,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="🗑 Evet, sil", callback_data=f"trip:cancel:{trip.id}:yes"),
            ]]),
        )
        return
    async with SessionFactory() as session:
        trip = await repo.get_user_trip(session, db_user.id, trip.id)
        await repo.deactivate_trip(session, trip)
    await callback.message.edit_text(f"🗑 {trip.destination} gezisi silindi.", parse_mode=None)
    await callback.answer()
