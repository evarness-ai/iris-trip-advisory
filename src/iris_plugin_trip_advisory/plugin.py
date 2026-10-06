"""Trip Advisory: one tool that consumes the ``weather.forecast`` capability.

The forecast comes through ``api.capability("weather.forecast")``, declared under
``capabilities: uses`` in ``manifest.yaml``; no other plugin is imported. When nothing
provides it ``api.capability`` returns ``None`` and the tool answers in a degraded way.
"""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from typing import Any

from iris_harness.sdk import PluginAPI
from iris_harness.sdk.capabilities import CapabilityUnavailable, Forecast

from . import advice

TOOL = "trip_advisory"
CAPABILITY = "weather.forecast"
MAX_DAYS = 14
DESCRIPTION = (
    "Suggest what to pack and which days suit outdoor plans for a trip, from the weather "
    'forecast. Args: {"location": str, "start": "YYYY-MM-DD", "end": "YYYY-MM-DD"}.'
)


def _parse(args: dict[str, Any]) -> tuple[str, date, date] | str:
    location = args.get("location")
    if not isinstance(location, str) or not location.strip():
        return 'error: pass the place as {"location": "Lisbon", "start": ..., "end": ...}'
    try:
        start = date.fromisoformat(str(args.get("start")))
        end = date.fromisoformat(str(args.get("end")))
    except ValueError:
        return "error: start and end must be dates like 2026-10-06"
    if end < start:
        return "error: end is before start"
    return location.strip(), start, end


def _days_ahead(end: date, today: date) -> int:
    """weather.forecast counts days from now, so the window must reach the trip's last day."""
    return (end - today).days + 1


def _run_coroutine(coro: Any) -> Any:
    """Drive an async capability call from a sync tool, whether or not a loop is running."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


def render(location: str, start: date, end: date, forecast: Forecast) -> str:
    periods = advice.in_range(forecast.periods, start, end)
    header = f"Trip to {forecast.location} ({start} to {end}):"
    if not periods:
        return (
            f"{header}\nThe forecast does not cover those dates yet, "
            "so pack for general conditions (light layers)."
        )
    good, indoor = advice.outdoor_plans(periods)
    lines = [header, "Pack: " + ", ".join(advice.packing(periods)) + "."]
    if good:
        lines.append("Good for outdoor plans: " + ", ".join(d.isoformat() for d in good) + ".")
    if indoor:
        lines.append(
            "Better indoors (rain or wind): " + ", ".join(d.isoformat() for d in indoor) + "."
        )
    return "\n".join(lines)


def make_tool(weather: Any, today: Any = date.today):
    def run(args: dict[str, Any]) -> str:
        parsed = _parse(args)
        if isinstance(parsed, str):
            return parsed
        location, start, end = parsed
        if weather is None:
            return (
                "Could not check the weather: no weather forecast service is installed. "
                "Suggestion without it: pack light layers and a rain jacket, and keep plans flexible."
            )
        days = min(_days_ahead(end, today()), MAX_DAYS)
        if days < 1:
            return "error: the trip is in the past"
        try:
            forecast = _run_coroutine(weather.forecast(location, days))
        except CapabilityUnavailable as exc:
            return f"Could not check the weather ({exc}). Pack light layers and a rain jacket."
        return render(location, start, end, forecast)

    return run


def setup(api: PluginAPI) -> None:
    api.register_tool(TOOL, DESCRIPTION, make_tool(api.capability(CAPABILITY)))
