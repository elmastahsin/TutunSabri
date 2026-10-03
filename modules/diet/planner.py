"""Daily meal plan generation and Telegram rendering."""

from __future__ import annotations

import html
import json
import logging
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Optional
from urllib.parse import quote_plus

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from core.database import SessionFactory
from modules.diet import repository as repo
from modules.diet.config import (
    ACTIVITY_LEVELS,
    GOALS,
    HISTORY_DAYS,
    MEAL_PATTERNS,
    MealSlot,
    MEAL_SLOT_BY_KEY,
    SEXES,
    SYSTEM_PROMPT,
)
from modules.ai.llm import generate_json, generate_text
from modules.diet.models import DietProfile


logger = logging.getLogger(__name__)
WEEKDAYS_TR = ("Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma", "Cumartesi", "Pazar")
# Share of the daily calorie target per slot (main meals carry most of it).
SLOT_CALORIE_SHARE = {"kahvalti": 0.25, "ara1": 0.10, "ogle": 0.30, "ara2": 0.10, "aksam": 0.25}


def slot_kcal(profile: DietProfile, calories: int) -> dict[str, int]:
    """Split the daily target over the profile's active meals."""
    slots = repo.active_slots(profile)
    total = sum(SLOT_CALORIE_SHARE[slot.key] for slot in slots)
    return {slot.key: round(calories * SLOT_CALORIE_SHARE[slot.key] / total) for slot in slots}


@dataclass(frozen=True)
class Targets:
    bmr: int
    tdee: int
    calories: int
    protein_g: int
    water_l: float


def compute_targets(profile: DietProfile) -> Targets:
    """Mifflin-St Jeor BMR, activity multiplier and a goal-based adjustment."""
    sex_offset = 5 if profile.sex == "male" else -161
    bmr = 10 * profile.weight_kg + 6.25 * profile.height_cm - 5 * profile.age + sex_offset
    tdee = bmr * ACTIVITY_LEVELS.get(profile.activity, ACTIVITY_LEVELS["light"])[1]
    if profile.goal == "lose":
        floor = 1500 if profile.sex == "male" else 1200
        calories = max(floor, tdee - 500)
        protein_per_kg = 1.6
    elif profile.goal == "gain":
        calories = tdee + 300
        protein_per_kg = 1.8
    else:
        calories = tdee
        protein_per_kg = 1.2
    return Targets(
        bmr=round(bmr),
        tdee=round(tdee),
        calories=int(round(calories / 50) * 50),
        protein_g=round(profile.weight_kg * protein_per_kg),
        water_l=round(profile.weight_kg * 0.033, 1),
    )


def describe_profile(profile: DietProfile) -> str:
    target = f"{profile.target_weight_kg:g} kg" if profile.target_weight_kg else "belirtilmedi"
    return "\n".join(
        (
            f"- Cinsiyet: {SEXES.get(profile.sex, profile.sex)}",
            f"- Yaş: {profile.age}",
            f"- Boy: {profile.height_cm} cm",
            f"- Güncel kilo: {profile.weight_kg:g} kg",
            f"- Hedef kilo: {target}",
            f"- Hedef: {GOALS.get(profile.goal, profile.goal)}",
            f"- Aktivite: {ACTIVITY_LEVELS.get(profile.activity, (profile.activity,))[0]}",
            f"- Öğün düzeni: {MEAL_PATTERNS.get(profile.meal_pattern or '', ('günde 5 öğün',))[0]}",
            f"- Alerji / kısıtlar: {profile.restrictions or 'yok'}",
            f"- Tercihler / sevmedikleri: {profile.preferences or 'yok'}",
        )
    )


def _meal_json_spec() -> str:
    return (
        '{"slot": "<öğün anahtarı>", "title": "Yemeğin kısa adı", '
        '"items": ["porsiyonuyla birlikte malzeme/yiyecek", "..."], '
        '"kcal": 450, "protein_g": 25, "prep_min": 15, '
        '"recipe_query": "internette aranacak tarif adı, ör. \'fırında tavuk sote\'", '
        '"tip": "tek cümlelik pratik ipucu"}'
    )


async def _history_context(user_id: int, plan_date: date) -> str:
    async with SessionFactory() as session:
        titles = await repo.get_recent_meal_titles(session, user_id, plan_date, HISTORY_DAYS)
        logs = await repo.get_logs_since(session, user_id, plan_date - timedelta(days=3))
    lines = []
    if titles:
        lines.append("Son günlerde önerilen yemekler (tekrar etme): " + "; ".join(titles[-25:]))
    checkins = [f"{log.log_date:%d.%m}: {log.value}" for log in logs if log.kind == "checkin"]
    if checkins:
        lines.append("Son günlerin plana uyum değerlendirmesi: " + ", ".join(checkins))
    skipped = [
        f"{log.log_date:%d.%m} {MEAL_SLOT_BY_KEY[log.slot].label}"
        for log in logs
        if log.kind == "meal" and log.value == "swapped" and log.slot in MEAL_SLOT_BY_KEY
    ]
    if skipped:
        lines.append("Kullanıcının değiştirmek istediği öğünler: " + ", ".join(skipped))
    return "\n".join(lines) or "Henüz geçmiş veri yok."


def _build_plan_prompt(profile: DietProfile, targets: Targets, plan_date: date, history: str) -> str:
    active = repo.active_slots(profile)
    kcal = slot_kcal(profile, targets.calories)
    slots = "\n".join(f"- {slot.key}: {slot.label} (~{kcal[slot.key]} kcal)" for slot in active)
    pattern_rule = ""
    if not any(slot.key.startswith("ara") for slot in active):
        pattern_rule = (
            f"- Danışan günde yalnızca {len(active)} öğün yiyor; ara öğün önerme. Öğünler "
            "doyurucu, lifli ve proteinden zengin olsun ki öğünler arasında acıkmasın.\n"
        )
    return f"""{plan_date:%d.%m.%Y} {WEEKDAYS_TR[plan_date.weekday()]} günü için bir günlük beslenme planı hazırla.

Danışan profili:
{describe_profile(profile)}

Hesaplanmış günlük hedefler:
- Enerji: ~{targets.calories} kcal (BMR {targets.bmr}, TDEE {targets.tdee})
- Protein: ~{targets.protein_g} g
- Su: ~{targets.water_l} litre

Geçmiş:
{history}

Öğünler (her biri için yaklaşık kalori):
{slots}

Kurallar:
- Alerji ve kısıtlara kesinlikle uy; sevmediği yiyecekleri önerme.
- Türk mutfağından, evde kolay hazırlanabilen, mevsimine uygun yemekler seç.
- Porsiyonları gram, adet veya kaşık gibi ölçülerle yaz.
{pattern_rule}- Ara öğünler (varsa) pratik olsun (meyve, yoğurt, kuruyemiş vb.).
- Son günlerde önerilenleri tekrar etme; çeşitlilik sağla.
- Ana öğünlerde recipe_query alanına bilinen bir tarif adı yaz; ara öğünlerde boş bırakabilirsin.
- URL yazma.

Yalnızca şu yapıda JSON döndür:
{{"note": "güne dair kısa, motive edici diyetisyen notu", "meals": [{_meal_json_spec()}, ...]}}
"meals" listesinde şu sırayla tam olarak bu slot anahtarları olsun: {", ".join(slot.key for slot in active)}."""


def _normalize_meal(raw: Any, slot_key: str) -> dict[str, Any]:
    raw = raw if isinstance(raw, dict) else {}
    items = raw.get("items") or []
    if isinstance(items, str):
        items = [items]

    def _int(value: Any) -> Optional[int]:
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return None

    return {
        "slot": slot_key,
        "title": str(raw.get("title") or MEAL_SLOT_BY_KEY[slot_key].label).strip(),
        "items": [str(item).strip() for item in items if str(item).strip()],
        "kcal": _int(raw.get("kcal")),
        "protein_g": _int(raw.get("protein_g")),
        "prep_min": _int(raw.get("prep_min")),
        "recipe_query": str(raw.get("recipe_query") or "").strip(),
        "tip": str(raw.get("tip") or "").strip(),
    }


def _normalize_plan(data: dict[str, Any], slots: list[MealSlot]) -> dict[str, Any]:
    raw_meals = data.get("meals") if isinstance(data.get("meals"), list) else []
    by_slot = {m.get("slot"): m for m in raw_meals if isinstance(m, dict)}
    meals = []
    for index, slot in enumerate(slots):
        raw = by_slot.get(slot.key)
        if raw is None and index < len(raw_meals):
            raw = raw_meals[index]
        meals.append(_normalize_meal(raw, slot.key))
    return {"note": str(data.get("note") or "").strip(), "meals": meals}


async def get_or_create_plan(
    profile: DietProfile, plan_date: date, force: bool = False
) -> dict[str, Any]:
    if not force:
        async with SessionFactory() as session:
            existing = await repo.get_plan(session, profile.user_id, plan_date)
        if existing is not None:
            plan = json.loads(existing.content)
            # A plan made for another meal pattern is stale; build a new one.
            if [m.get("slot") for m in plan.get("meals", [])] == [
                slot.key for slot in repo.active_slots(profile)
            ]:
                return plan

    targets = compute_targets(profile)
    history = await _history_context(profile.user_id, plan_date)
    data, source = await generate_json(
        _build_plan_prompt(profile, targets, plan_date, history), SYSTEM_PROMPT
    )
    plan = _normalize_plan(data, repo.active_slots(profile))
    plan["targets"] = {"kcal": targets.calories, "protein_g": targets.protein_g, "water_l": targets.water_l}
    async with SessionFactory() as session:
        await repo.save_plan(session, profile.user_id, plan_date, plan, source)
    logger.info("Diet plan created user_id=%s date=%s source=%s", profile.user_id, plan_date, source)
    return plan


async def swap_meal(profile: DietProfile, plan_date: date, slot_key: str) -> dict[str, Any]:
    """Replace one meal of the stored plan with a fresh alternative."""
    plan = await get_or_create_plan(profile, plan_date)
    current = next(m for m in plan["meals"] if m["slot"] == slot_key)
    others = ", ".join(m["title"] for m in plan["meals"] if m["slot"] != slot_key)
    targets = compute_targets(profile)
    kcal = current.get("kcal") or slot_kcal(profile, targets.calories).get(slot_key)
    prompt = f"""Danışan bugünkü {MEAL_SLOT_BY_KEY[slot_key].label} önerisini ("{current['title']}") değiştirmek istiyor.
Yaklaşık {kcal} kcal olan, tamamen farklı bir alternatif öner.

Danışan profili:
{describe_profile(profile)}

Günün diğer öğünleri: {others}

Alerji ve kısıtlara kesinlikle uy, URL yazma. Yalnızca şu yapıda JSON döndür:
{_meal_json_spec().replace('<öğün anahtarı>', slot_key)}"""
    data, _ = await generate_json(prompt, SYSTEM_PROMPT, max_tokens=1024)
    new_meal = _normalize_meal(data, slot_key)
    plan["meals"] = [new_meal if m["slot"] == slot_key else m for m in plan["meals"]]
    async with SessionFactory() as session:
        await repo.save_plan(session, profile.user_id, plan_date, plan, "swap")
        await repo.add_log(session, profile.user_id, plan_date, "meal", "swapped", slot=slot_key)
    return new_meal


async def ask_dietitian(profile: Optional[DietProfile], question: str) -> str:
    context = describe_profile(profile) if profile else "Profil bilgisi yok."
    prompt = f"""Danışan profili:
{context}

Danışanın sorusu: {question}

Kısa, net ve uygulanabilir yanıt ver (en fazla 1500 karakter). Markdown kullanma."""
    return await generate_text(prompt, SYSTEM_PROMPT)


# --- Rendering -------------------------------------------------------------


def recipe_links(meal: dict[str, Any]) -> str:
    query = meal.get("recipe_query") or ""
    if not query:
        return ""
    if "tarif" not in query.casefold():
        query = f"{query} tarifi"
    recipe = f"https://www.google.com/search?q={quote_plus(query)}"
    video = f"https://www.youtube.com/results?search_query={quote_plus(query)}"
    return f'<a href="{html.escape(recipe)}">📖 Tarif</a>  ·  <a href="{html.escape(video)}">▶️ Video</a>'


def _meal_stats(meal: dict[str, Any]) -> str:
    parts = []
    if meal.get("kcal"):
        parts.append(f"~{meal['kcal']} kcal")
    if meal.get("protein_g"):
        parts.append(f"{meal['protein_g']} g protein")
    if meal.get("prep_min"):
        parts.append(f"⏱ {meal['prep_min']} dk")
    return " · ".join(parts)


def render_meal(meal: dict[str, Any], meal_time: str) -> str:
    slot = MEAL_SLOT_BY_KEY[meal["slot"]]
    lines = [
        f"{slot.emoji} <b>{slot.label} zamanı!</b> ({meal_time})",
        "",
        f"<b>{html.escape(meal['title'])}</b>",
    ]
    lines.extend(f"• {html.escape(item)}" for item in meal["items"])
    stats = _meal_stats(meal)
    if stats:
        lines.extend(("", f"<i>{stats}</i>"))
    if meal.get("tip"):
        lines.extend(("", f"💡 {html.escape(meal['tip'])}"))
    links = recipe_links(meal)
    if links:
        lines.extend(("", links))
    return "\n".join(lines)


def meal_keyboard(plan_date: date, slot_key: str) -> InlineKeyboardMarkup:
    stamp = plan_date.strftime("%Y%m%d")
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Yedim", callback_data=f"diet:eat:{stamp}:{slot_key}"),
                InlineKeyboardButton(text="⏭ Atladım", callback_data=f"diet:skip:{stamp}:{slot_key}"),
                InlineKeyboardButton(text="🔄 Değiştir", callback_data=f"diet:swap:{stamp}:{slot_key}"),
            ]
        ]
    )


def render_daily_list(plan: dict[str, Any], plan_date: date, meal_times: dict[str, str]) -> str:
    lines = [f"📋 <b>{plan_date:%d.%m.%Y} {WEEKDAYS_TR[plan_date.weekday()]} — Günün Öğün Listesi</b>"]
    targets = plan.get("targets") or {}
    if targets:
        lines.append(
            f"<i>Hedef: ~{targets.get('kcal')} kcal · {targets.get('protein_g')} g protein · "
            f"{targets.get('water_l')} L su</i>"
        )
    if plan.get("note"):
        lines.extend(("", f"🩺 {html.escape(plan['note'])}"))
    for meal in plan["meals"]:
        slot = MEAL_SLOT_BY_KEY[meal["slot"]]
        lines.extend(("", f"{slot.emoji} <b>{meal_times.get(slot.key, '')} · {slot.label}</b>"))
        lines.append(html.escape(meal["title"]))
        if meal["items"]:
            lines.append("<i>" + html.escape(", ".join(meal["items"])) + "</i>")
        links = recipe_links(meal)
        if links:
            lines.append(links)
    lines.extend(("", "Her öğün saatinde ayrıca hatırlatma göndereceğim. 🔔"))
    return "\n".join(lines)
