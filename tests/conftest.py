"""A stand-in ``weather.forecast`` provider, mounted as a plugin of its own.

The consumer's tests must not import the real weather plugin (it is another package, and
the point is that nothing but the capability connects them), so the provider here is a
tiny plugin with its own manifest that implements the published Protocol.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from iris_harness.sdk import PluginAPI
from iris_harness.sdk.capabilities import CapabilityUnavailable, Forecast, ForecastPeriod
from iris_harness.testing import plugin

PROVIDER_MANIFEST = {
    "name": "fake-weather",
    "version": "0.0.0",
    "party": "trusted-third-party",
    "capabilities": {"provides": ["weather.forecast"]},
}


def day(offset: int) -> date:
    return date.today() + timedelta(days=offset)


def period(offset: int, *, temp: float, rain: float, wind: float, summary: str) -> ForecastPeriod:
    start = datetime.combine(day(offset), datetime.min.time(), tzinfo=UTC)
    return ForecastPeriod(start, start + timedelta(days=1), temp, rain, wind, summary)


class FakeWeather:
    """Tomorrow is sunny and warm; the day after is wet and windy."""

    def __init__(self, fail: bool = False) -> None:
        self.calls: list[tuple[str, int]] = []
        self.fail = fail

    async def forecast(self, location: str, days: int = 3) -> Forecast:
        self.calls.append((location, days))
        if self.fail:
            raise CapabilityUnavailable("weather service is down")
        return Forecast(
            location=f"{location}, Portugal",
            issued_at=datetime.now(UTC),
            periods=(
                period(1, temp=26.0, rain=0.05, wind=10.0, summary="Clear sky"),
                period(2, temp=14.0, rain=0.8, wind=45.0, summary="Heavy rain"),
            ),
        )


@pytest.fixture
def fake_weather() -> FakeWeather:
    return FakeWeather()


def provider_plugin(fake: FakeWeather):
    def setup(api: PluginAPI) -> None:
        api.provide("weather.forecast", fake)

    return plugin(setup, manifest=PROVIDER_MANIFEST)
