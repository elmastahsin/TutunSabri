"""Static configuration for the travel guide module."""

from __future__ import annotations

MAX_TRIP_DAYS = 14
MORNING_BRIEF_TIME = "08:00"  # destination local time
PACKING_REMINDER_TIME = "20:00"  # local time, the evening before departure
SCHEDULER_TICK_SECONDS = 60
EVENT_GRACE_MINUTES = 90

COMPANIONS = {
    "solo": "Yalnız",
    "partner": "Partnerimle",
    "friends": "Arkadaşlarla",
    "family": "Ailece (çocuklu)",
}
INTERESTS = {
    "history": "🏛 Tarih & müze",
    "nature": "🌲 Doğa & yürüyüş",
    "food": "🍽 Yeme-içme",
    "art": "🎨 Sanat & kültür",
    "shopping": "🛍 Alışveriş",
    "night": "🌃 Gece hayatı",
    "beach": "🏖 Deniz & plaj",
    "photo": "📸 Fotoğraf noktaları",
}
BUDGETS = {
    "low": "💸 Ekonomik",
    "mid": "💳 Orta",
    "high": "💎 Konforlu",
}

SYSTEM_PROMPT = (
    "Sen deneyimli, pratik ve samimi bir seyahat rehberisin. Rotaları coğrafi olarak "
    "mantıklı sıralar, yürüme ve ulaşım sürelerini hesaba katar, günleri aşırı "
    "doldurmazsın. Yalnızca gerçekten var olan, bilinen yerleri önerirsin; emin "
    "olmadığın mekan isimleri uydurmazsın. Fiyatları yaklaşık ve güncel olmayabilir "
    "diye belirtirsin. Her zaman Türkçe yanıt verirsin."
)
