"""P1: a plugin that asks for a tool it was not granted is denied by the caller policy.

The grant lives in the CALLER's manifest (``uses: {tools: [...]}``); an operator can only take
grants away (``config/governance/tool-access.yaml``), never add one. Each denial is proven three
ways: the call comes back ``held`` with the policy's reason, the ledger has the deny row naming
the stamped caller, and the tool's own code (here: the weather plugin's HTTP) never ran.
"""

from __future__ import annotations

import shutil
from importlib.resources import files
from pathlib import Path

import pytest
from iris_harness.testing import harness

from .support import (
    CapabilityProbe,
    FakeWeather,
    OpenMeteo,
    answer_rule,
    call_tool_rule,
    day,
    provider_plugin,
    raw_rows,
    script,
    trip_plugin,
    weather_plugin,
)

FORECAST_ARGS = {"location": "Lisbon", "days": 2}


def _rows(h, tool: str, hook: str = "pre_tool_use") -> list[dict]:
    return [r for r in raw_rows(h, tool=tool) if r["hook"] == hook]


def test_a_plugin_without_a_grant_is_denied_and_the_tool_never_runs() -> None:
    meteo = OpenMeteo()
    thief = CapabilityProbe("thief")  # no `uses: tools`
    with harness(plugins=[weather_plugin(meteo.transport()), thief.plugin()]) as h:
        result = thief.api.tools.call("weather_forecast", FORECAST_ARGS)
        assert not result.ok and result.held and result.approval_id is None
        assert "caller_policy" in result.text and "plugin:thief" in result.text
        assert meteo.requests == []  # the tool's code did not run: no HTTP left the plugin
        denies = [r for r in _rows(h, "weather_forecast") if r["decision"] == "deny"]
        assert [(r["plugin"], r["payload"]["caller"]) for r in denies] == [
            ("caller_policy", "plugin:thief")
        ]
        assert not _rows(h, "weather_forecast", "post_tool_use")  # nothing ran, nothing "used"


def test_the_same_plugin_with_the_grant_is_allowed() -> None:
    meteo = OpenMeteo()
    friend = CapabilityProbe("friend", {"uses": {"tools": ["weather_forecast"]}})
    with harness(plugins=[weather_plugin(meteo.transport()), friend.plugin()]) as h:
        result = friend.api.tools.call("weather_forecast", FORECAST_ARGS)
        assert result.ok and "Forecast for Lisbon" in result.text
        assert len(meteo.requests) == 2
        assert {r["payload"]["caller"] for r in _rows(h, "weather_forecast")} == {"plugin:friend"}
        # a grant is for the named tool only
        other = friend.api.tools.call("weather_code_meaning", {"code": 3})
        assert other.held and "caller_policy" in other.text


def test_a_plugin_always_may_call_its_own_tools_but_not_a_made_up_caller() -> None:
    with harness(plugins=[weather_plugin(OpenMeteo().transport())]) as h:
        service = h._runtime.tool_service
        assert service.for_caller("plugin:weather-now").call("weather_code_meaning", {"code": 3}).ok
        ghost = service.for_caller("plugin:not-mounted").call("weather_code_meaning", {"code": 3})
        assert ghost.held and "is not a mounted plugin" in ghost.text


def test_a_grant_for_a_tool_nobody_registers_grants_nothing() -> None:
    friend = CapabilityProbe("friend", {"uses": {"tools": ["no_such_tool"]}})
    with harness(plugins=[friend.plugin()]):
        result = friend.api.tools.call("no_such_tool", {})
        assert not result.ok and "unknown tool" in result.text


# -- the operator can take a grant away, and cannot add one ---------------------------------------
def _config_with_denials(tmp_path: Path, denials: dict[str, list[str]]) -> Path:
    """A copy of the packaged config with ``governance/tool-access.yaml`` added."""
    packaged = Path(str(files("iris_harness") / "_data" / "config"))
    config = tmp_path / "config"
    shutil.copytree(packaged, config)
    lines = ["deny:"] + [f"  {p}: [{', '.join(t)}]" for p, t in denials.items()]
    (config / "governance").mkdir(exist_ok=True)
    (config / "governance" / "tool-access.yaml").write_text("\n".join(lines) + "\n")
    return config


def test_an_operator_denial_overrides_the_plugins_own_grant(tmp_path: Path) -> None:
    meteo = OpenMeteo()
    friend = CapabilityProbe("friend", {"uses": {"tools": ["weather_forecast"]}})
    config = _config_with_denials(tmp_path, {"friend": ["weather_forecast"]})
    with harness(
        plugins=[weather_plugin(meteo.transport()), friend.plugin()], config_dir=config
    ) as h:
        result = friend.api.tools.call("weather_forecast", FORECAST_ARGS)
        assert result.held and not result.ok, result
        assert meteo.requests == []
        assert [
            r["decision"] for r in _rows(h, "weather_forecast") if r["plugin"] == "caller_policy"
        ] == ["deny"]


@pytest.mark.parametrize(
    "what", ["capability:weather.forecast", "capability:weather.forecast.forecast"]
)
def test_an_operator_can_take_a_capability_away_and_the_consumer_degrades(
    tmp_path: Path, what: str
) -> None:
    """Whole capability or one method: the REAL consumer, which declared ``uses``, still mounts
    and answers (degraded) because ``CapabilityDenied`` is a ``CapabilityUnavailable``."""
    fake = FakeWeather()
    config = _config_with_denials(tmp_path, {"trip-advisory": [what]})
    args = {"location": "Lisbon", "start": str(day(1)), "end": str(day(2))}
    rules = script(
        answer_rule("Could not check the weather", "Degraded advice."),
        call_tool_rule("Plan my trip to Lisbon", "trip_advisory", args),
    )
    with harness(
        plugins=[provider_plugin(fake), trip_plugin()], fake_model=rules, config_dir=config
    ) as h:
        assert h.chat("Plan my trip to Lisbon").text == "Degraded advice."
        assert fake.calls == []  # the provider was never reached
        cap = "capability:weather.forecast.forecast"
        denies = [r for r in raw_rows(h, tool=cap) if r["decision"] == "deny"]
        assert denies and {r["payload"]["caller"] for r in denies} == {"plugin:trip-advisory"}
        assert (
            "the operator took" in denies[0]["reason"] and "tool-access.yaml" in denies[0]["reason"]
        )


def test_a_malformed_operator_file_fails_closed(tmp_path: Path) -> None:
    config = _config_with_denials(tmp_path, {})
    (config / "governance" / "tool-access.yaml").write_text("deny: [this is, not a mapping\n")
    friend = CapabilityProbe("friend", {"uses": {"tools": ["weather_code_meaning"]}})
    with harness(plugins=[weather_plugin(), friend.plugin()], config_dir=config):
        result = friend.api.tools.call("weather_code_meaning", {"code": 3})
        assert result.held and "unreadable" in result.text, result
