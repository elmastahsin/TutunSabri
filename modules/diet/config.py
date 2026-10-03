"""Static configuration for the dietitian module."""

from __future__ import annotations

from dataclasses import dataclass
from zoneinfo import ZoneInfo


TIMEZONE = ZoneInfo("Europe/Istanbul")


@dataclass(frozen=True)
class MealSlot:
    key: str
    label: str
    emoji: str
    default_time: str
    is_main: bool


MEAL_SLOTS = (
    MealSlot("kahvalti", "Kahvaltı", "🍳", "08:30", True),
    MealSlot("ara1", "Kuşluk ara öğünü", "🍎", "11:00", False),
    MealSlot("ogle", "Öğle yemeği", "🥗", "13:00", True),
    MealSlot("ara2", "İkindi ara öğünü", "🥜", "16:30", False),
    MealSlot("aksam", "Akşam yemeği", "🍲", "19:30", True),
)
MEAL_SLOT_BY_KEY = {slot.key: slot for slot in MEAL_SLOTS}

# Meal pattern -> (label, active slot keys, default times overriding the slot defaults).
MEAL_PATTERNS = {
    "2": ("2 öğün (kahvaltı + akşam)", ("kahvalti", "aksam"), {"kahvalti": "10:00", "aksam": "19:00"}),
    "3": ("3 ana öğün", ("kahvalti", "ogle", "aksam"), {}),
    "5": ("3 ana + 2 ara öğün", ("kahvalti", "ara1", "ogle", "ara2", "aksam"), {}),
}
DEFAULT_MEAL_PATTERN = "5"

DAILY_LIST_LEAD_MINUTES = 30
WATER_TIMES = ("10:00", "12:00", "15:00", "17:30", "21:00")
CHECKIN_TIME = "21:45"
WEIGH_IN_WEEKDAY = 6  # Sunday
WEIGH_IN_TIME = "09:00"

SCHEDULER_TICK_SECONDS = 30
EVENT_GRACE_MINUTES = 30
HISTORY_DAYS = 5

GOALS = {
    "lose": "Kilo vermek",
    "maintain": "Kilomu korumak",
    "gain": "Kilo / kas almak",
}
ACTIVITY_LEVELS = {
    "sedentary": ("Hareketsiz (masa başı)", 1.2),
    "light": ("Hafif aktif (haftada 1-3 gün)", 1.375),
    "moderate": ("Orta aktif (haftada 3-5 gün)", 1.55),
    "high": ("Çok aktif (haftada 6-7 gün)", 1.725),
}
SEXES = {"male": "Erkek", "female": "Kadın"}

SYSTEM_PROMPT = (
    "Sen Türkiye'de çalışan deneyimli, sıcak ve pratik bir diyetisyensin. "
    "Türk mutfağına ve Türkiye'deki marketlerde kolay bulunan malzemelere öncelik "
    "verirsin. Önerilerin dengeli, uygulanabilir ve bilimsel beslenme ilkelerine "
    "uygundur. Kullanıcının alerjilerine ve kısıtlarına kesinlikle uyarsın. "
    "Tıbbi teşhis koymazsın; ciddi sağlık sorunlarında doktora yönlendirirsin. "
    "Her zaman Türkçe yanıt verirsin."
)
