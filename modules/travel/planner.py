"""Itinerary generation and Telegram rendering for trips."""

from __future__ import annotations

import html
import json
import logging
from datetime import date, timedelta
from typing import Any, Optional
from urllib.parse import quote_plus

from core.database import SessionFactory
from modules.ai.llm import LLMUnavailableError, generate_json
from modules.diet import repository as diet_repo
from modules.diet.config import GOALS
from modules.travel import repository as repo
from modules.travel.config import BUDGETS, COMPANIONS, INTERESTS, SYSTEM_PROMPT
from modules.travel.models import Trip
from modules.travel.weather import DayForecast, Place, forecast, geocode


logger = logging.getLogger(__name__)
WEEKDAYS_TR = ("Pzt", "Sal", "Çar", "Per", "Cum", "Cmt", "Paz")


async def resolve_destination(text: str) -> Optional[Place]:
    """Turn free text (a city, region or landmark) into a geocoded place."""
    prompt = f"""Kullanıcının seyahat hedefi: "{text}"
Hava durumu için bu hedefi temsil eden şehir veya kasabayı bul.
Bölge adlarında (ör. Kapadokya, Toskana, Bali) bölgedeki en uygun merkezi yerleşimi seç.
Yalnızca şu yapıda JSON döndür:
{{"search_name": "yerleşim adı, yerel yazımıyla", "country_code": "ISO 3166-1 alpha-2 kodu"}}"""
    try:
        data, _ = await generate_json(prompt, SYSTEM_PROMPT, max_tokens=200)
        place = await geocode(str(data.get("search_name") or text), str(data.get("country_code") or "") or None)
        if place is not None:
            return place
    except LLMUnavailableError:
        logger.warning("Destination could not be normalized by AI", exc_info=True)
    return await geocode(text)


def trip_days(trip: Trip) -> list[date]:
    return [trip.start_date + timedelta(days=offset) for offset in range((trip.end_date - trip.start_date).days + 1)]


def _labels(keys: str, options: dict[str, str]) -> str:
    return ", ".join(options[key].split(" ", 1)[-1] for key in keys.split(",") if key in options)


async def _diet_context(user_id: int) -> str:
    async with SessionFactory() as session:
        profile = await diet_repo.get_profile(session, user_id)
    if profile is None:
        return ""
    parts = [f"hedef: {GOALS.get(profile.goal, profile.goal)}"]
    if profile.restrictions:
        parts.append(f"kısıtlar: {profile.restrictions}")
    if profile.preferences:
        parts.append(f"tercihler: {profile.preferences}")
    return "Gezginin beslenme profili (yeme-içme önerilerinde dikkate al): " + "; ".join(parts)


def _forecast_text(days: dict[date, DayForecast]) -> str:
    if not days:
        return "Tarihler için hava tahmini henüz yok; o ayın tipik iklimine göre düşün."
    return "\n".join(f"- {day:%d.%m}: {item.line()}" for day, item in sorted(days.items()))


def _build_prompt(trip: Trip, today: date, weather: dict[date, DayForecast], diet: str) -> str:
    day_count = len(trip_days(trip))
    international = (trip.country_code or "").upper() != "TR"
    practical_hint = (
        "Vize/giriş şartı (Türk vatandaşı için, kesin olmayan bilgiyi doğrulamasını söyle), "
        "para birimi ve kur, şehir içi ulaşım, bahşiş ve güvenlik notları"
        if international
        else "şehir içi ulaşım, ulaşım kartı, yoğunluk ve rezervasyon gerektiren yerler"
    )
    return f"""{trip.destination} ({trip.country or ''}) için {day_count} günlük bir gezi planı hazırla.

Tarihler: {trip.start_date:%d.%m.%Y} - {trip.end_date:%d.%m.%Y} (bugün {today:%d.%m.%Y})
Kiminle: {COMPANIONS.get(trip.companions, trip.companions)}
İlgi alanları: {_labels(trip.interests, INTERESTS) or 'genel'}
Bütçe seviyesi: {BUDGETS.get(trip.budget, trip.budget).split(' ', 1)[-1]}
Ek notlar: {trip.notes or 'yok'}
{diet}

Hava tahmini:
{_forecast_text(weather)}

Kurallar:
- Her gün coğrafi olarak yakın yerleri grupla; günde 3-6 durak yeterli.
- Yağmurlu günlere kapalı mekanları koy.
- Durak ve mekan adlarını Google Maps'te bulunabilecek şekilde resmi adlarıyla yaz.
- Yeme-içmede yöresel lezzetleri öne çıkar; yalnızca köklü ve çok bilinen mekanları isimle öner,
  emin değilsen mekan yerine "semt + yemek türü" yaz.
- Fiyatları yerel para biriminde, kişi başı ve yaklaşık ver.
- Pratik bilgilerde şunlara değin: {practical_hint}.
- Bavul listesini hava durumuna ve aktivitelere göre hazırla.
- URL yazma.

Yalnızca şu yapıda JSON döndür:
{{
  "summary": "2-3 cümlelik gezi özeti",
  "practical": ["kısa pratik bilgi", "..."],
  "local_dishes": [{{"name": "yemek adı", "note": "kısa açıklama"}}],
  "days": [
    {{
      "day": 1,
      "title": "günün teması",
      "stops": [{{"time": "09:00", "name": "yer adı", "note": "neden gidilmeli, ne kadar kalınmalı", "cost": "ücretsiz / ~200 TL"}}],
      "food": [{{"meal": "öğle", "name": "mekan veya yemek", "area": "semt", "note": "kısa not"}}]
    }}
  ],
  "packing": ["bavul maddesi", "..."],
  "budget": {{
    "currency": "para birimi kodu",
    "per_person_total": 0,
    "items": [{{"label": "Konaklama", "amount": 0}}, {{"label": "Yeme-içme", "amount": 0}}, {{"label": "Giriş ücretleri", "amount": 0}}, {{"label": "Şehir içi ulaşım", "amount": 0}}],
    "note": "kısa bütçe notu"
  }}
}}
"days" listesinde tam olarak {day_count} gün olsun."""


def _as_list(value: Any) -> list:
    return value if isinstance(value, list) else []


def _normalize(data: dict[str, Any], day_count: int) -> dict[str, Any]:
    days = [d for d in _as_list(data.get("days")) if isinstance(d, dict)][:day_count]
    while len(days) < day_count:
        days.append({"title": "Serbest gün", "stops": [], "food": []})
    for index, day in enumerate(days, start=1):
        day["day"] = index
        day["stops"] = [s for s in _as_list(day.get("stops")) if isinstance(s, dict) and s.get("name")]
        day["food"] = [f for f in _as_list(day.get("food")) if isinstance(f, dict) and f.get("name")]
    budget = data.get("budget") if isinstance(data.get("budget"), dict) else {}
    return {
        "summary": str(data.get("summary") or ""),
        "practical": [str(x) for x in _as_list(data.get("practical"))],
        "local_dishes": [x for x in _as_list(data.get("local_dishes")) if isinstance(x, dict) and x.get("name")],
        "days": days,
        "packing": [str(x) for x in _as_list(data.get("packing"))],
        "budget": budget,
    }


async def build_plan(trip: Trip, today: date) -> dict[str, Any]:
    weather = await forecast(trip.latitude, trip.longitude, trip.start_date, trip.end_date, today)
    data, source = await generate_json(
        _build_prompt(trip, today, weather, await _diet_context(trip.user_id)), SYSTEM_PROMPT, max_tokens=8192
    )
    plan = _normalize(data, len(trip_days(trip)))
    async with SessionFactory() as session:
        await repo.save_trip_plan(session, trip.id, plan)
    trip.plan = json.dumps(plan, ensure_ascii=False)
    logger.info("Trip plan created trip_id=%s source=%s", trip.id, source)
    return plan


def plan_of(trip: Trip) -> dict[str, Any]:
    return json.loads(trip.plan or "{}")


# --- Rendering -------------------------------------------------------------


def maps_link(name: str, area: str, trip: Trip, label: str = "📍") -> str:
    query = ", ".join(part for part in (name, area, trip.destination) if part)
    url = f"https://www.google.com/maps/search/?api=1&query={quote_plus(query)}"
    return f'<a href="{html.escape(url)}">{label}</a>'


def _e(value: Any) -> str:
    return html.escape(str(value or ""))


def render_overview(trip: Trip, plan: dict[str, Any], weather: dict[date, DayForecast]) -> str:
    days = trip_days(trip)
    lines = [
        f"🧳 <b>{_e(trip.destination)}</b> · {trip.start_date:%d.%m} – {trip.end_date:%d.%m.%Y} ({len(days)} gün)",
        f"<i>{_e(COMPANIONS.get(trip.companions))} · {_e(BUDGETS.get(trip.budget))}</i>",
    ]
    if plan.get("summary"):
        lines.extend(("", _e(plan["summary"])))
    lines.extend(("", "🗓 <b>Günler</b>"))
    for day_date, day in zip(days, plan["days"]):
        forecast_line = f" — {weather[day_date].emoji_text[0]} {round(weather[day_date].t_max)}°" if day_date in weather else ""
        lines.append(f"{day['day']}. gün ({day_date:%d.%m} {WEEKDAYS_TR[day_date.weekday()]}): {_e(day.get('title'))}{forecast_line}")
    if plan.get("local_dishes"):
        lines.extend(("", "🥘 <b>Mutlaka tat</b>"))
        lines.extend(f"• <b>{_e(d['name'])}</b> — {_e(d.get('note'))}" for d in plan["local_dishes"][:6])
    budget = plan.get("budget") or {}
    if budget.get("per_person_total"):
        currency = _e(budget.get("currency"))
        lines.extend(("", f"💰 <b>Tahmini bütçe:</b> kişi başı ~{_e(budget['per_person_total'])} {currency}"))
        lines.extend(
            f"• {_e(item.get('label'))}: ~{_e(item.get('amount'))} {currency}"
            for item in budget.get("items", []) if isinstance(item, dict)
        )
        if budget.get("note"):
            lines.append(f"<i>{_e(budget['note'])}</i>")
    if plan.get("practical"):
        lines.extend(("", "ℹ️ <b>Pratik bilgiler</b>"))
        lines.extend(f"• {_e(item)}" for item in plan["practical"])
    lines.extend(("", "<i>Fiyat ve açılış saatleri değişebilir; gitmeden kontrol etmeni öneririm.</i>"))
    return "\n".join(lines)


def render_day(trip: Trip, plan: dict[str, Any], index: int, weather: Optional[DayForecast], greeting: bool = False) -> str:
    day = plan["days"][index]
    day_date = trip_days(trip)[index]
    header = f"☀️ <b>Günaydın! {_e(trip.destination)} — {day['day']}. gün</b>" if greeting else f"🗓 <b>{day['day']}. gün</b>"
    lines = [f"{header} ({day_date:%d.%m} {WEEKDAYS_TR[day_date.weekday()]})", f"<i>{_e(day.get('title'))}</i>"]
    if weather is not None:
        lines.append(f"Hava: {weather.line()}")
    if day["stops"]:
        lines.append("")
        for stop in day["stops"]:
            cost = f" · {_e(stop.get('cost'))}" if stop.get("cost") else ""
            lines.append(f"<b>{_e(stop.get('time'))}</b> {_e(stop['name'])} {maps_link(stop['name'], '', trip)}{cost}")
            if stop.get("note"):
                lines.append(f"   <i>{_e(stop['note'])}</i>")
    if day["food"]:
        lines.extend(("", "🍽 <b>Yeme-içme</b>"))
        for food in day["food"]:
            lines.append(
                f"• {_e(food.get('meal')).capitalize()}: {_e(food['name'])}"
                f"{' (' + _e(food.get('area')) + ')' if food.get('area') else ''} "
                f"{maps_link(food['name'], food.get('area', ''), trip)}"
            )
            if food.get("note"):
                lines.append(f"   <i>{_e(food['note'])}</i>")
    return "\n".join(lines)


def render_packing(trip: Trip, plan: dict[str, Any], weather: dict[date, DayForecast]) -> str:
    lines = [f"🎒 <b>{_e(trip.destination)} bavul listesi</b>"]
    if weather:
        lines.extend(("", "🌦 <b>Hava tahmini</b>"))
        lines.extend(f"{day:%d.%m}: {item.line()}" for day, item in sorted(weather.items()))
    lines.append("")
    lines.extend(f"☐ {_e(item)}" for item in plan.get("packing", []))
    lines.extend(("", "☐ Kimlik / pasaport, şarj aleti, ilaçlar"))
    return "\n".join(lines)
