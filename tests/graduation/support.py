"""Shared pieces of the graduation suite (issue #81).

Both REAL plugins are mounted through the way IRIS finds an installed plugin (their
``iris_harness.plugins`` entry points); everything else here is a TEST-ONLY plugin that
lives in this directory and ships in neither package: a write tool that needs approval, a
plugin that raises, a provider that returns owner identity, and so on.

Only the stable tier of IRIS is imported (``test_p0_stable_imports`` holds this file to it).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from importlib import import_module
from importlib.metadata import entry_points
from pathlib import Path
from types import ModuleType
from typing import Any

import httpx
from iris_harness.sdk import PluginAPI
from iris_harness.sdk.audit import AuditLog
from iris_harness.sdk.capabilities import Forecast, ForecastPeriod
from iris_harness.testing import Harness, plugin

GROUP = "iris_harness.plugins"
WEATHER = "weather-now"
TRIP = "trip-advisory"


# --------------------------------------------------------------- the two real plugins
def entry_point(name: str):
    found = [e for e in entry_points(group=GROUP) if e.name == name]
    assert found, f"{name!r} is not installed (no {GROUP} entry point)"
    return found[0]


def module_of(name: str) -> ModuleType:
    """The plugin's own module (``...plugin``), found through its entry point."""
    return import_module(entry_point(name).module)


def manifest_of(name: str) -> Path:
    return Path(module_of(name).__file__).with_name("manifest.yaml")


def trip_plugin(manifest: Any = None):
    """The real trip-advisory plugin, as the loader would mount it."""
    return plugin(entry_point(TRIP).load(), manifest=manifest or manifest_of(TRIP))


def weather_plugin(transport: httpx.MockTransport | None = None):
    """The real weather-now plugin. With a ``transport`` its HTTP goes there (the plugin's
    own ``make_setup`` seam); with none it is exactly what the entry point loads."""
    setup = module_of(WEATHER).make_setup(transport, transport) if transport else None
    return plugin(setup or entry_point(WEATHER).load(), manifest=manifest_of(WEATHER))


# ------------------------------------------------------------- canned Open-Meteo data
def day(offset: int) -> date:
    return date.today() + timedelta(days=offset)


class OpenMeteo:
    """A canned Open-Meteo: records every request the plugin sends, answers from memory."""

    def __init__(self, place: str = "Lisbon") -> None:
        self.place = place
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.host == "geocoding-api.open-meteo.com":
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "name": self.place,
                            "latitude": 38.7,
                            "longitude": -9.1,
                            "country": "Portugal",
                            "admin1": "Lisboa",
                        }
                    ]
                },
            )
        if request.url.host == "api.open-meteo.com":
            days = [day(i) for i in range(0, 4)]
            return httpx.Response(
                200,
                json={
                    "utc_offset_seconds": 0,
                    "daily": {
                        "time": [d.isoformat() for d in days],
                        "weather_code": [0, 0, 61, 3],
                        "temperature_2m_max": [24.0, 26.0, 14.0, 20.0],
                        "temperature_2m_min": [15.0, 16.0, 9.0, 12.0],
                        "precipitation_probability_max": [5, 5, 80, 20],
                        "wind_speed_10m_max": [10.0, 10.0, 45.0, 15.0],
                    },
                },
            )
        return httpx.Response(404)

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self)


# ------------------------------------------------------------ a stand-in weather provider
PROVIDER_MANIFEST = {
    "name": "fake-weather",
    "version": "0.0.0",
    "party": "trusted-third-party",
    "capabilities": {"provides": ["weather.forecast"]},
}


class FakeWeather:
    """Tomorrow is sunny; the day after is wet. ``location`` / ``summary`` are overridable
    so a test can have the provider return text it controls."""

    def __init__(self, *, location: str = "Lisbon, Portugal", summary: str = "Clear sky") -> None:
        self.calls: list[tuple[str, int]] = []
        self.location = location
        self.summary = summary
        self.fail: Exception | None = None

    async def forecast(self, location: str, days: int = 3) -> Forecast:
        self.calls.append((location, days))
        if self.fail is not None:
            raise self.fail
        start = datetime.combine(day(1), datetime.min.time(), tzinfo=UTC)
        periods = (
            ForecastPeriod(start, start + timedelta(days=1), 26.0, 0.05, 10.0, self.summary),
            ForecastPeriod(
                start + timedelta(days=1), start + timedelta(days=2), 14.0, 0.8, 45.0, "Heavy rain"
            ),
        )
        return Forecast(location=self.location, issued_at=datetime.now(UTC), periods=periods)


def provider_plugin(fake: FakeWeather, *, extra: Callable[[PluginAPI], None] | None = None):
    def setup(api: PluginAPI) -> None:
        api.provide("weather.forecast", fake)
        if extra is not None:
            extra(api)

    return plugin(setup, manifest=PROVIDER_MANIFEST)


# ---------------------------------------------------------- model scripts for chat turns
def script(*rules: dict[str, Any]) -> dict[str, Any]:
    return {"rules": list(rules)}


def call_tool_rule(user: str, tool: str, args: dict[str, Any], *, name: str | None = None):
    return {
        "name": name or f"call {tool}",
        "match": {"user": f"User: {user}"},
        "reply": {
            "content": f"Thought: Use the tool.\nAction: {tool}\nAction Input: {json.dumps(args)}"
        },
    }


def answer_rule(observation_regex: str, answer: str, *, name: str = "answer"):
    return {
        "name": name,
        "match": {"user": rf"(?s)Observation:.*{observation_regex}"},
        "reply": {"content": f"Thought: Done.\nFinal Answer: {answer}"},
    }


# --------------------------------------------------------------------- ledger readers
def raw_rows(h: Harness, *, hook: str | None = None, tool: str | None = None) -> list[dict]:
    """The ledger's rows as dicts with their payload decoded (caller, args_digest, ...)."""
    out = []
    for row in AuditLog(db_path=h.audit_db).query(since=h._started):
        payload = json.loads(row.payload_json)
        if hook is not None and row.hook_point != hook:
            continue
        if tool is not None and payload.get("tool_name") != tool:
            continue
        out.append(
            {
                "id": row.id,
                "hook": row.hook_point,
                "plugin": row.plugin,
                "decision": row.decision,
                "reason": row.reason,
                "tier": row.tier,
                "agent": row.agent_type,
                "severity": row.severity,
                "payload": payload,
            }
        )
    return out


def hooks_for(h: Harness, tool: str) -> set[str]:
    return {r["hook"] for r in raw_rows(h, tool=tool)}


def callers_for(h: Harness, tool: str) -> set[str]:
    """Who the harness stamped on the tool's PRE/POST rows."""
    return {
        r["payload"].get("caller")
        for r in raw_rows(h, tool=tool)
        if r["hook"] in ("pre_tool_use", "post_tool_use")
    }


def record(h: Harness, name: str):
    """The plugin registry's record of ``name`` (status, failure_count, last_error, ...).

    The registry is not in the stable tier; read only through ``Harness._runtime`` the way
    ``iris_harness.testing.conformance`` itself does, to count what the fault boundary caught.
    """
    return next(r for r in h._runtime.plugin_registry.plugins() if r.name == name)


class CapabilityProbe:
    """Hands a test the PluginAPI the harness bound to a probe plugin, so code can call
    ``api.tools`` / ``api.capability`` as that plugin would."""

    def __init__(self, name: str, manifest: dict[str, Any] | None = None) -> None:
        self.api: PluginAPI | None = None
        self.name = name
        self._manifest = {"name": name, "version": "0.0.0", **(manifest or {})}

    def plugin(self):
        def setup(api: PluginAPI) -> None:
            self.api = api

        return plugin(setup, manifest=self._manifest)


def health_text(h: Harness, probe: CapabilityProbe) -> str:
    """``system_health`` as the model would read it, called through a mounted probe plugin
    (``probe`` must declare ``uses: {tools: [system_health]}``)."""
    assert probe.api is not None and probe.api.tools is not None
    result = probe.api.tools.call("system_health", {})
    assert result.ok, result.text
    return result.text


def health_line(text: str, plugin_name: str) -> str:
    """The ``plugin:<name>`` row of the health report (empty when it has none)."""
    return next((ln for ln in text.splitlines() if ln.startswith(f"- plugin:{plugin_name} ")), "")


HEALTH_PROBE = {"uses": {"tools": ["system_health"]}}
