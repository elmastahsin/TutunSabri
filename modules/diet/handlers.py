"""Telegram handlers for the dietitian: profile wizard, menu and reminders' buttons."""

from __future__ import annotations

import html
import json
import logging
import re
from datetime import date, datetime
from typing import Optional

from aiogram import F, Router
from aiogram.filters import Command, StateFilter
from aiogram.filters.command import CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from core.database import SessionFactory
from core.models import User
from modules.ai.handlers import split_message
from modules.diet import repository as repo
from modules.diet.config import ACTIVITY_LEVELS, GOALS, MEAL_PATTERNS, SEXES, TIMEZONE
from modules.ai.llm import LLMUnavailableError
from modules.diet.models import DietProfile
from modules.diet.planner import (
    ask_dietitian,
    compute_targets,
    describe_profile,
    get_or_create_plan,
    meal_keyboard,
    render_daily_list,
    render_meal,
    swap_meal,
)


logger = logging.getLogger(__name__)
router = Router(name="diet")
TIME_PATTERN = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
CHECKIN_LABELS = {"full": "tam uydu", "partial": "kısmen uydu", "none": "uymadı"}


class DietSetup(StatesGroup):
    sex = State()
    age = State()
    height = State()
    weight = State()
    target_weight = State()
    goal = State()
    activity = State()
    meal_pattern = State()
    restrictions = State()
    preferences = State()


class DietWeight(StatesGroup):
    waiting = State()


def _today() -> date:
    return datetime.now(TIMEZONE).date()


def _choice_keyboard(prefix: str, options: dict) -> InlineKeyboardMarkup:
    rows = []
    for key, value in options.items():
        label = value[0] if isinstance(value, tuple) else value
        rows.append([InlineKeyboardButton(text=label, callback_data=f"dset:{prefix}:{key}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _skip_keyboard(field: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="Yok / Geç", callback_data=f"dset:skip:{field}")]]
    )


def _menu_keyboard(profile: DietProfile) -> InlineKeyboardMarkup:
    toggle = "🔕 Bildirimleri durdur" if profile.is_enabled else "🔔 Bildirimleri başlat"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="📋 Bugünün planı", callback_data="diet:today"),
                InlineKeyboardButton(text="🔄 Yeni plan", callback_data="diet:regen"),
            ],
            [
                InlineKeyboardButton(text="👤 Profilim", callback_data="diet:profile"),
                InlineKeyboardButton(text="✏️ Profili düzenle", callback_data="diet:edit"),
            ],
            [
                InlineKeyboardButton(text="🍽 Öğün düzeni", callback_data="diet:pattern"),
                InlineKeyboardButton(text="⏰ Öğün saatleri", callback_data="diet:times"),
            ],
            [InlineKeyboardButton(text="⚙️ Hatırlatmalar", callback_data="diet:reminders")],
            [
                InlineKeyboardButton(text="⚖️ Kilo gir", callback_data="diet:weight"),
                InlineKeyboardButton(text=toggle, callback_data="diet:toggle:is_enabled"),
            ],
        ]
    )


def _reminders_keyboard(profile: DietProfile) -> InlineKeyboardMarkup:
    def row(field: str, label: str) -> list[InlineKeyboardButton]:
        mark = "✅" if getattr(profile, field) else "❌"
        return [InlineKeyboardButton(text=f"{mark} {label}", callback_data=f"diet:toggle:{field}")]

    return InlineKeyboardMarkup(
        inline_keyboard=[
            row("water_enabled", "Su hatırlatmaları"),
            row("checkin_enabled", "Akşam günlük kontrolü"),
            row("weighin_enabled", "Pazar tartı hatırlatması"),
            [InlineKeyboardButton(text="⬅️ Menü", callback_data="diet:menu")],
        ]
    )


async def _load_profile(db_user: User) -> Optional[DietProfile]:
    async with SessionFactory() as session:
        return await repo.get_profile(session, db_user.id)


def _menu_text(profile: DietProfile) -> str:
    targets = compute_targets(profile)
    status = "açık 🔔" if profile.is_enabled else "kapalı 🔕"
    return (
        "🩺 <b>Diyetisyenin</b>\n\n"
        f"Günlük hedef: <b>~{targets.calories} kcal</b> · {targets.protein_g} g protein · "
        f"{targets.water_l} L su\n"
        f"Bildirimler: {status}\n\n"
        "Soru sormak için: <code>/diyetsor akşam acıkırsam ne yiyebilirim?</code>"
    )


async def _send_menu(message: Message, profile: DietProfile) -> None:
    await message.answer(_menu_text(profile), parse_mode="HTML", reply_markup=_menu_keyboard(profile))


# --- Entry points ----------------------------------------------------------


@router.message(Command("diyet"))
async def handle_diet(message: Message, state: FSMContext, db_user: User) -> None:
    await state.clear()
    profile = await _load_profile(db_user)
    if profile is None:
        await message.answer(
            "🩺 Merhaba! Ben senin diyetisyeninim.\n\n"
            "Sana her öğün ne yemen gerektiğini tarifleriyle birlikte göndereceğim; "
            "su, günlük kontrol ve haftalık tartı hatırlatmaları da yapacağım.\n\n"
            "Önce seni tanımam gerek, birkaç kısa soru soracağım.",
            parse_mode=None,
        )
        await _start_wizard(message, state)
        return
    await _send_menu(message, profile)


async def _start_wizard(message: Message, state: FSMContext) -> None:
    await state.set_state(DietSetup.sex)
    await message.answer("1/10 · Cinsiyetin?", parse_mode=None, reply_markup=_choice_keyboard("sex", SEXES))


# --- Profile wizard --------------------------------------------------------


def _parse_number(text: Optional[str], low: float, high: float) -> Optional[float]:
    try:
        value = float((text or "").replace(",", ".").strip())
    except ValueError:
        return None
    return value if low <= value <= high else None


@router.callback_query(DietSetup.sex, F.data.startswith("dset:sex:"))
async def setup_sex(callback: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(sex=callback.data.split(":")[2])
    await state.set_state(DietSetup.age)
    await callback.message.edit_text("2/10 · Kaç yaşındasın?", parse_mode=None)
    await callback.answer()


@router.message(DietSetup.age)
async def setup_age(message: Message, state: FSMContext) -> None:
    value = _parse_number(message.text, 14, 100)
    if value is None:
        await message.answer("Lütfen yaşını sayı olarak yaz (ör. 29).", parse_mode=None)
        return
    await state.update_data(age=int(value))
    await state.set_state(DietSetup.height)
    await message.answer("3/10 · Boyun kaç cm? (ör. 178)", parse_mode=None)


@router.message(DietSetup.height)
async def setup_height(message: Message, state: FSMContext) -> None:
    value = _parse_number(message.text, 120, 230)
    if value is None:
        await message.answer("Lütfen boyunu cm cinsinden yaz (ör. 178).", parse_mode=None)
        return
    await state.update_data(height_cm=int(value))
    await state.set_state(DietSetup.weight)
    await message.answer("4/10 · Şu anki kilon? (ör. 82.5)", parse_mode=None)


@router.message(DietSetup.weight)
async def setup_weight(message: Message, state: FSMContext) -> None:
    value = _parse_number(message.text, 30, 300)
    if value is None:
        await message.answer("Lütfen kilonu kg cinsinden yaz (ör. 82.5).", parse_mode=None)
        return
    await state.update_data(weight_kg=round(value, 1))
    await state.set_state(DietSetup.target_weight)
    await message.answer(
        "5/10 · Hedef kilon var mı? (ör. 75) Yoksa geçebilirsin.",
        parse_mode=None,
        reply_markup=_skip_keyboard("target_weight"),
    )


@router.message(DietSetup.target_weight)
async def setup_target_weight(message: Message, state: FSMContext) -> None:
    value = _parse_number(message.text, 30, 300)
    if value is None:
        await message.answer("Lütfen hedef kilonu yaz (ör. 75) ya da 'Geç' butonuna bas.", parse_mode=None)
        return
    await state.update_data(target_weight_kg=round(value, 1))
    await _ask_goal(message, state)


async def _ask_goal(message: Message, state: FSMContext) -> None:
    await state.set_state(DietSetup.goal)
    await message.answer("6/10 · Hedefin nedir?", parse_mode=None, reply_markup=_choice_keyboard("goal", GOALS))


@router.callback_query(DietSetup.goal, F.data.startswith("dset:goal:"))
async def setup_goal(callback: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(goal=callback.data.split(":")[2])
    await state.set_state(DietSetup.activity)
    await callback.message.edit_text(
        "7/10 · Günlük aktivite seviyen?",
        parse_mode=None,
        reply_markup=_choice_keyboard("activity", ACTIVITY_LEVELS),
    )
    await callback.answer()


def _pattern_options() -> dict[str, str]:
    return {key: label for key, (label, _, _) in MEAL_PATTERNS.items()}


@router.callback_query(DietSetup.activity, F.data.startswith("dset:activity:"))
async def setup_activity(callback: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(activity=callback.data.split(":")[2])
    await state.set_state(DietSetup.meal_pattern)
    await callback.message.edit_text(
        "8/10 · Günde genelde kaç öğün yiyorsun?",
        parse_mode=None,
        reply_markup=_choice_keyboard("pattern", _pattern_options()),
    )
    await callback.answer()


@router.callback_query(DietSetup.meal_pattern, F.data.startswith("dset:pattern:"))
async def setup_meal_pattern(callback: CallbackQuery, state: FSMContext) -> None:
    pattern = callback.data.split(":")[2]
    if pattern not in MEAL_PATTERNS:
        await callback.answer()
        return
    await state.update_data(meal_pattern=pattern)
    await state.set_state(DietSetup.restrictions)
    await callback.message.edit_text(
        "9/10 · Alerjin, intoleransın veya sağlık kısıtın var mı?\n"
        "(ör. laktoz intoleransı, gluten, şeker hastalığı, vejetaryenim)",
        parse_mode=None,
        reply_markup=_skip_keyboard("restrictions"),
    )
    await callback.answer()


@router.message(DietSetup.restrictions)
async def setup_restrictions(message: Message, state: FSMContext) -> None:
    await state.update_data(restrictions=(message.text or "").strip()[:500] or None)
    await _ask_preferences(message, state)


async def _ask_preferences(message: Message, state: FSMContext) -> None:
    await state.set_state(DietSetup.preferences)
    await message.answer(
        "10/10 · Sevmediğin yiyecekler veya tercihlerin?\n"
        "(ör. balık sevmem, öğlen iş yerinde yiyorum, pratik tarifler olsun)",
        parse_mode=None,
        reply_markup=_skip_keyboard("preferences"),
    )


@router.message(DietSetup.preferences)
async def setup_preferences(message: Message, state: FSMContext, db_user: User) -> None:
    await state.update_data(preferences=(message.text or "").strip()[:500] or None)
    await _finish_wizard(message, state, db_user)


@router.callback_query(StateFilter(DietSetup), F.data.startswith("dset:skip:"))
async def setup_skip(callback: CallbackQuery, state: FSMContext, db_user: User) -> None:
    field = callback.data.split(":")[2]
    await state.update_data(**{field if field != "target_weight" else "target_weight_kg": None})
    await callback.answer()
    await callback.message.edit_reply_markup(reply_markup=None)
    if field == "target_weight":
        await _ask_goal(callback.message, state)
    elif field == "restrictions":
        await _ask_preferences(callback.message, state)
    else:
        await _finish_wizard(callback.message, state, db_user)


async def _finish_wizard(message: Message, state: FSMContext, db_user: User) -> None:
    data = await state.get_data()
    await state.clear()
    async with SessionFactory() as session:
        profile = await repo.save_profile(session, db_user.id, data)
        await repo.add_log(session, db_user.id, _today(), "weight", f"{profile.weight_kg:g}")
    targets = compute_targets(profile)
    await message.answer(
        "✅ <b>Profilin kaydedildi!</b>\n\n"
        f"{html.escape(describe_profile(profile))}\n\n"
        f"🔥 Bazal metabolizma: {targets.bmr} kcal\n"
        f"🏃 Günlük harcama: {targets.tdee} kcal\n"
        f"🎯 Günlük hedef: <b>~{targets.calories} kcal</b>, {targets.protein_g} g protein, {targets.water_l} L su\n\n"
        "Her sabah ilk öğünden önce günün listesini, her öğün saatinde de o öğünü göndereceğim.\n"
        "Saatleri ⏰ Öğün saatleri menüsünden görüp /diyetsaat ile değiştirebilirsin.\n"
        "<i>Not: Bu öneriler genel bilgilendirme amaçlıdır; sağlık sorunun varsa doktoruna danış.</i>",
        parse_mode="HTML",
    )
    await _send_menu(message, profile)


# --- Menu callbacks --------------------------------------------------------


async def _require_profile(callback: CallbackQuery, db_user: User) -> Optional[DietProfile]:
    profile = await _load_profile(db_user)
    if profile is None:
        await callback.answer("Önce /diyet ile profilini oluştur.", show_alert=True)
    return profile


@router.callback_query(F.data == "diet:menu")
async def cb_menu(callback: CallbackQuery, db_user: User) -> None:
    profile = await _require_profile(callback, db_user)
    if profile is None:
        return
    await callback.message.edit_text(_menu_text(profile), parse_mode="HTML", reply_markup=_menu_keyboard(profile))
    await callback.answer()


@router.callback_query(F.data.in_({"diet:today", "diet:regen"}))
async def cb_plan(callback: CallbackQuery, db_user: User) -> None:
    profile = await _require_profile(callback, db_user)
    if profile is None:
        return
    force = callback.data == "diet:regen"
    await callback.answer("Plan hazırlanıyor..." if force else None)
    progress = await callback.message.answer("🩺 Planına bakıyorum...", parse_mode=None)
    try:
        plan = await get_or_create_plan(profile, _today(), force=force)
    except LLMUnavailableError:
        await progress.edit_text("Plan şu anda hazırlanamadı; AI servisine ulaşılamadı.", parse_mode=None)
        return
    chunks = iter(split_message(render_daily_list(plan, _today(), repo.meal_times_of(profile))))
    await progress.edit_text(next(chunks), parse_mode="HTML", disable_web_page_preview=True)
    for chunk in chunks:
        await callback.message.answer(chunk, parse_mode="HTML", disable_web_page_preview=True)


@router.callback_query(F.data == "diet:profile")
async def cb_profile(callback: CallbackQuery, db_user: User) -> None:
    profile = await _require_profile(callback, db_user)
    if profile is None:
        return
    async with SessionFactory() as session:
        weights = await repo.get_weight_history(session, db_user.id)
    history = "\n".join(f"  {log.log_date:%d.%m}: {log.value} kg" for log in weights) or "  henüz yok"
    await callback.message.answer(
        f"👤 <b>Profilin</b>\n\n{html.escape(describe_profile(profile))}\n\n⚖️ Kilo geçmişi:\n{history}",
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "diet:edit")
async def cb_edit(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await callback.message.answer("Profilini baştan güncelleyelim.", parse_mode=None)
    await _start_wizard(callback.message, state)


@router.callback_query(F.data == "diet:times")
async def cb_times(callback: CallbackQuery, db_user: User) -> None:
    profile = await _require_profile(callback, db_user)
    if profile is None:
        return
    await callback.message.answer(_times_text(profile), parse_mode="HTML")
    await callback.answer()


def _times_text(profile: DietProfile) -> str:
    times = repo.meal_times_of(profile)
    lines = ["⏰ <b>Öğün saatlerin</b>", ""]
    lines.extend(
        f"{slot.emoji} {times[slot.key]} · {slot.label} (<code>{slot.key}</code>)"
        for slot in repo.active_slots(profile)
    )
    lines.extend(("", "Değiştirmek için: <code>/diyetsaat kahvalti 09:00</code>"))
    return "\n".join(lines)


@router.callback_query(F.data == "diet:pattern")
async def cb_pattern(callback: CallbackQuery, db_user: User) -> None:
    profile = await _require_profile(callback, db_user)
    if profile is None:
        return
    rows = [
        [InlineKeyboardButton(
            text=("✅ " if key == profile.meal_pattern else "") + label,
            callback_data=f"diet:setpattern:{key}",
        )]
        for key, label in _pattern_options().items()
    ]
    rows.append([InlineKeyboardButton(text="⬅️ Menü", callback_data="diet:menu")])
    await callback.message.edit_text(
        "🍽 Günde kaç öğün yemek istiyorsun?", parse_mode=None,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("diet:setpattern:"))
async def cb_set_pattern(callback: CallbackQuery, db_user: User) -> None:
    pattern = callback.data.split(":")[2]
    profile = await _require_profile(callback, db_user)
    if profile is None or pattern not in MEAL_PATTERNS:
        return
    async with SessionFactory() as session:
        profile = await repo.save_profile(session, db_user.id, {"meal_pattern": pattern})
    await callback.answer("Öğün düzeni güncellendi")
    await callback.message.edit_text(_menu_text(profile), parse_mode="HTML", reply_markup=_menu_keyboard(profile))
    await callback.message.answer(
        _times_text(profile) + "\n\nBugünün planı yeni düzene göre yeniden hazırlanacak.",
        parse_mode="HTML",
    )


@router.callback_query(F.data == "diet:reminders")
async def cb_reminders(callback: CallbackQuery, db_user: User) -> None:
    profile = await _require_profile(callback, db_user)
    if profile is None:
        return
    await callback.message.edit_text(
        "⚙️ Hangi hatırlatmalar gelsin?", parse_mode=None, reply_markup=_reminders_keyboard(profile)
    )
    await callback.answer()


TOGGLE_FIELDS = {"is_enabled", "water_enabled", "checkin_enabled", "weighin_enabled"}


@router.callback_query(F.data.startswith("diet:toggle:"))
async def cb_toggle(callback: CallbackQuery, db_user: User) -> None:
    field = callback.data.split(":")[2]
    profile = await _require_profile(callback, db_user)
    if profile is None or field not in TOGGLE_FIELDS:
        return
    async with SessionFactory() as session:
        profile = await repo.save_profile(session, db_user.id, {field: not getattr(profile, field)})
    if field == "is_enabled":
        await callback.message.edit_reply_markup(reply_markup=_menu_keyboard(profile))
        await callback.answer("Bildirimler açıldı 🔔" if profile.is_enabled else "Bildirimler durduruldu 🔕")
    else:
        await callback.message.edit_reply_markup(reply_markup=_reminders_keyboard(profile))
        await callback.answer("Güncellendi")


# --- Meal / check-in / weight callbacks ------------------------------------


def _parse_stamp(stamp: str) -> date:
    return datetime.strptime(stamp, "%Y%m%d").date()


@router.callback_query(F.data.regexp(r"^diet:(eat|skip):\d{8}:\w+$"))
async def cb_meal_status(callback: CallbackQuery, db_user: User) -> None:
    _, action, stamp, slot_key = callback.data.split(":")
    async with SessionFactory() as session:
        await repo.add_log(
            session, db_user.id, _parse_stamp(stamp), "meal",
            "eaten" if action == "eat" else "skipped", slot=slot_key,
        )
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.answer("Afiyet olsun! 😋" if action == "eat" else "Not aldım, bir sonraki öğüne odaklanalım.")


@router.callback_query(F.data.regexp(r"^diet:swap:\d{8}:\w+$"))
async def cb_meal_swap(callback: CallbackQuery, db_user: User) -> None:
    _, _, stamp, slot_key = callback.data.split(":")
    profile = await _require_profile(callback, db_user)
    if profile is None:
        return
    if slot_key not in repo.meal_times_of(profile):
        await callback.answer("Bu öğün artık öğün düzeninde yok.", show_alert=True)
        return
    await callback.answer("Alternatif hazırlanıyor...")
    plan_date = _parse_stamp(stamp)
    try:
        meal = await swap_meal(profile, plan_date, slot_key)
    except LLMUnavailableError:
        await callback.message.answer("Şu an alternatif üretemedim, biraz sonra tekrar dene.", parse_mode=None)
        return
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer(
        render_meal(meal, repo.meal_times_of(profile)[slot_key]),
        parse_mode="HTML",
        disable_web_page_preview=True,
        reply_markup=meal_keyboard(plan_date, slot_key),
    )


@router.callback_query(F.data.regexp(r"^diet:check:\d{8}:(full|partial|none)$"))
async def cb_checkin(callback: CallbackQuery, db_user: User) -> None:
    _, _, stamp, answer = callback.data.split(":")
    async with SessionFactory() as session:
        await repo.add_log(session, db_user.id, _parse_stamp(stamp), "checkin", CHECKIN_LABELS[answer])
    replies = {
        "full": "Harika! 🎉 Böyle devam.",
        "partial": "Gayet iyi, her gün bir adım. 💪",
        "none": "Sorun değil, yarın yeni bir gün. Planı buna göre ayarlayacağım. 🌱",
    }
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer(replies[answer], parse_mode=None)
    await callback.answer()


@router.callback_query(F.data == "diet:weight")
async def cb_weight(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(DietWeight.waiting)
    await callback.message.answer("⚖️ Güncel kilonu yaz (ör. 78.4):", parse_mode=None)
    await callback.answer()


async def _record_weight(message: Message, db_user: User, raw: Optional[str]) -> bool:
    value = _parse_number(raw, 30, 300)
    if value is None:
        return False
    profile = await _load_profile(db_user)
    if profile is None:
        await message.answer("Önce /diyet ile profilini oluştur.", parse_mode=None)
        return True
    previous = profile.weight_kg
    async with SessionFactory() as session:
        await repo.add_log(session, db_user.id, _today(), "weight", f"{value:g}")
        profile = await repo.save_profile(session, db_user.id, {"weight_kg": round(value, 1)})
    diff = profile.weight_kg - previous
    trend = "değişim yok" if abs(diff) < 0.05 else f"{diff:+.1f} kg"
    extra = ""
    if profile.target_weight_kg:
        extra = f"\n🎯 Hedefe kalan: {abs(profile.weight_kg - profile.target_weight_kg):.1f} kg"
    targets = compute_targets(profile)
    await message.answer(
        f"✅ Kaydedildi: {profile.weight_kg:g} kg ({trend}){extra}\n"
        f"Yeni günlük hedefin: ~{targets.calories} kcal",
        parse_mode=None,
    )
    return True


@router.message(DietWeight.waiting)
async def weight_input(message: Message, state: FSMContext, db_user: User) -> None:
    if not await _record_weight(message, db_user, message.text):
        await message.answer("Lütfen kilonu sayı olarak yaz (ör. 78.4).", parse_mode=None)
        return
    await state.clear()


@router.message(Command("kilo"))
async def handle_weight(message: Message, command: CommandObject, db_user: User) -> None:
    if not await _record_weight(message, db_user, command.args):
        await message.answer("Kullanım: /kilo 78.4", parse_mode=None)


@router.message(Command("diyetsaat"))
async def handle_meal_time(message: Message, command: CommandObject, db_user: User) -> None:
    profile = await _load_profile(db_user)
    if profile is None:
        await message.answer("Önce /diyet ile profilini oluştur.", parse_mode=None)
        return
    parts = (command.args or "").split()
    if len(parts) != 2 or parts[0] not in repo.meal_times_of(profile) or not TIME_PATTERN.match(parts[1]):
        await message.answer(_times_text(profile), parse_mode="HTML")
        return
    times = repo.meal_times_of(profile)
    times[parts[0]] = parts[1]
    async with SessionFactory() as session:
        profile = await repo.save_profile(
            session, db_user.id, {"meal_times": json.dumps(times, ensure_ascii=False)}
        )
    await message.answer("✅ Güncellendi.\n\n" + _times_text(profile), parse_mode="HTML")


@router.message(Command("diyetsor"))
async def handle_ask(message: Message, command: CommandObject, db_user: User) -> None:
    question = (command.args or "").strip()
    if not question:
        await message.answer("Kullanım: /diyetsor akşam tatlı krizine ne yapabilirim?", parse_mode=None)
        return
    profile = await _load_profile(db_user)
    progress = await message.answer("🩺 Düşünüyorum...", parse_mode=None)
    try:
        answer = await ask_dietitian(profile, question[:1000])
    except LLMUnavailableError:
        await progress.edit_text("Şu an yanıt veremiyorum; AI servisine ulaşılamadı.", parse_mode=None)
        return
    chunks = iter(split_message(answer))
    await progress.edit_text(next(chunks), parse_mode=None)
    for chunk in chunks:
        await message.answer(chunk, parse_mode=None)
