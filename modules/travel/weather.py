"""Geocoding and daily forecasts from the free, key-less Open-Meteo APIs."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional

import httpx


logger = logging.getLogger(__name__)
GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
FORECAST_HORIZON_DAYS = 15
REQUEST_TIMEOUT = 20.0


@dataclass(frozen=True)
class Place:
    name: str
    country: Optional[str]
    country_code: Optional[str]
    admin: Optional[str]
    latitude: float
    longitude: float
    timezone: str

    @property
    def label(self) -> str:
        parts = [self.name]
        if self.admin and self.admin != self.name:
            parts.append(self.admin)
        if self.country:
            parts.append(self.country)
        return ", ".join(parts)


@dataclass(frozen=True)
class DayForecast:
    day: date
    code: int
    t_min: float
    t_max: float
    rain_chance: Optional[int]

    @property
    def emoji_text(self) -> tuple[str, str]:
        return describe_code(self.code)

    def line(self) -> str:
        emoji, text = self.emoji_text
        rain = f", yağış ihtimali %{self.rain_chance}" if self.rain_chance is not None else ""
        return f"{emoji} {text}, {round(self.t_min)}° / {round(self.t_max)}°{rain}"


def describe_code(code: int) -> tuple[str, str]:
    """Map WMO weather codes to Turkish descriptions."""
    if code == 0:
        return "☀️", "Açık"
    if code in (1, 2):
        return "🌤", "Parçalı bulutlu"
    if code == 3:
        return "☁️", "Bulutlu"
    if code in (45, 48):
        return "🌫", "Sisli"
    if 51 <= code <= 57:
        return "🌦", "Çisenti"
    if 61 <= code <= 67 or 80 <= code <= 82:
        return "🌧", "Yağmurlu"
    if 71 <= code <= 77 or code in (85, 86):
        return "❄️", "Karlı"
    if code >= 95:
        return "⛈", "Gök gürültülü fırtına"
    return "🌡", "Değişken"


async def geocode(query: str, country_code: Optional[str] = None) -> Optional[Place]:
    params = {"name": query, "count": 1, "language": "tr", "format": "json"}
    if country_code:
        params["countryCode"] = country_code.upper()
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        response = await client.get(GEOCODING_URL, params=params)
        response.raise_for_status()
    results = response.json().get("results") or []
    if not results:
        return None
    first = results[0]
    return Place(
        name=first.get("name", query),
        country=first.get("country"),
        country_code=first.get("country_code"),
        admin=first.get("admin1"),
        latitude=float(first["latitude"]),
        longitude=float(first["longitude"]),
        timezone=first.get("timezone") or "UTC",
    )


async def forecast(latitude: float, longitude: float, start: date, end: date, today: date) -> dict[date, DayForecast]:
    """Daily forecast for the overlap of [start, end] with the forecast horizon."""
    horizon_end = today + timedelta(days=FORECAST_HORIZON_DAYS)
    start, end = max(start, today), min(end, horizon_end)
    if start > end:
        return {}
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            response = await client.get(
                FORECAST_URL,
                params={
                    "latitude": latitude,
                    "longitude": longitude,
                    "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
                    "timezone": "auto",
                    "start_date": start.isoformat(),
                    "end_date": end.isoformat(),
                },
            )
            response.raise_for_status()
        daily = response.json()["daily"]
    except (httpx.HTTPError, KeyError, ValueError):
        logger.warning("Weather forecast unavailable", exc_info=True)
        return {}
    days: dict[date, DayForecast] = {}
    rain = daily.get("precipitation_probability_max") or [None] * len(daily["time"])
    for index, raw_day in enumerate(daily["time"]):
        if daily["weather_code"][index] is None:
            continue
        day = date.fromisoformat(raw_day)
        days[day] = DayForecast(
            day=day,
            code=int(daily["weather_code"][index]),
            t_min=float(daily["temperature_2m_min"][index]),
            t_max=float(daily["temperature_2m_max"][index]),
            rain_chance=None if rain[index] is None else int(rain[index]),
        )
    return days
