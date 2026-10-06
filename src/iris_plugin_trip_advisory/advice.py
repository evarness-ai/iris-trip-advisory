"""Turn forecast periods into a packing and outdoor-plans suggestion: plain Python, no I/O."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

from iris_harness.sdk.capabilities import ForecastPeriod

RAIN_CHANCE = 0.5
COLD_C = 10.0
WARM_C = 25.0
WINDY_KPH = 40.0


def in_range(periods: Sequence[ForecastPeriod], start: date, end: date) -> list[ForecastPeriod]:
    return [p for p in periods if start <= p.start.date() <= end]


def packing(periods: Sequence[ForecastPeriod]) -> list[str]:
    items: list[str] = []
    if any((p.precipitation_probability or 0.0) >= RAIN_CHANCE for p in periods):
        items.append("a rain jacket and an umbrella")
    if any(p.temperature_c <= COLD_C for p in periods):
        items.append("a warm layer")
    if any(p.temperature_c >= WARM_C for p in periods):
        items.append("sunscreen and a hat")
    if any((p.wind_speed_kph or 0.0) >= WINDY_KPH for p in periods):
        items.append("a windproof layer")
    return items or ["light layers"]


def outdoor_plans(periods: Sequence[ForecastPeriod]) -> tuple[list[date], list[date]]:
    """(good outdoor days, days better spent indoors)."""
    good: list[date] = []
    indoor: list[date] = []
    for p in periods:
        wet = (p.precipitation_probability or 0.0) >= RAIN_CHANCE
        windy = (p.wind_speed_kph or 0.0) >= WINDY_KPH
        (indoor if wet or windy else good).append(p.start.date())
    return good, indoor
