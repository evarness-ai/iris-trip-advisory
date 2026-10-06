"""P0: an undeclared tool is refused; a plugin that raises is contained.

The fault boundary is the harness's: nothing in a plugin has to be written defensively for a
turn to survive it. What is asserted is (a) the turn is still answered, (b) the failure is
charged to the right plugin and visible in System Health with its reason, (c) a sibling plugin
is untouched, (d) the refused code never ran.
"""

from __future__ import annotations

from typing import Any

import pytest
from iris_harness.sdk import PluginAPI
from iris_harness.testing import harness, plugin

from .support import (
    HEALTH_PROBE,
    CapabilityProbe,
    FakeWeather,
    OpenMeteo,
    answer_rule,
    call_tool_rule,
    day,
    health_line,
    health_text,
    hooks_for,
    provider_plugin,
    record,
    script,
    trip_plugin,
    weather_plugin,
)

CODE_ASK = "Explain WMO code 61"


# -- P0 5: an undeclared tool is refused ----------------------------------------------------
def test_an_undeclared_tool_is_refused_never_run_and_shown_in_health() -> None:
    ran: list[str] = []

    def setup(api: PluginAPI) -> None:
        api.register_tool("declared", "A declared tool.", lambda a: "ok")
        api.register_tool("ghost", "Not in the manifest.", lambda a: ran.append("ghost") or "boo")

    manifest = {"name": "ghosty", "provides": ["tool"], "tools": {"declared": {"effect": "read"}}}
    probe = CapabilityProbe("probe", HEALTH_PROBE)
    with harness(plugins=[plugin(setup, manifest=manifest), probe.plugin()]) as h:
        rec = record(h, "ghosty")
        # the plugin is degraded (not failed to load): its declared tool is still served
        assert rec.status.value == "degraded"
        assert "ghost" in (rec.last_error or "") and "not declared" in (rec.last_error or "")
        assert [r.name for r in rec.registrations] == ["declared"]
        tools = h._runtime.tool_service.for_caller("plugin:ghosty")
        assert tools.call("declared", {}).ok
        refused = tools.call("ghost", {})
        assert not refused.ok and "unknown tool" in refused.text
        assert ran == []
        line = health_line(health_text(h, probe), "ghosty")
        assert "[yellow]" in line and "not declared" in line, line


def test_the_model_cannot_reach_an_undeclared_tool_either() -> None:
    ran: list[str] = []

    def setup(api: PluginAPI) -> None:
        api.register_tool("ghost", "Not in the manifest.", lambda a: ran.append("ghost") or "boo")

    rules = script(call_tool_rule("Do the ghost thing", "ghost", {}))
    with harness(plugins=[plugin(setup, manifest={"name": "ghosty"})], fake_model=rules) as h:
        result = h.chat("Do the ghost thing")
        assert result.answered
        assert ran == []
        assert "post_tool_use" not in hooks_for(h, "ghost")  # nothing ran, nothing was "used"


# -- P0 6: a plugin that raises is contained --------------------------------------------------
def _boomer() -> Any:
    def setup(api: PluginAPI) -> None:
        def boom(args: dict[str, Any]) -> str:
            raise RuntimeError("kaboom")

        api.register_tool("boom", "Always raises.", boom)

    return plugin(
        setup,
        manifest={"name": "boomer", "provides": ["tool"], "tools": {"boom": {"effect": "read"}}},
    )


def test_a_raising_tool_degrades_that_plugin_only_and_the_turn_survives() -> None:
    probe = CapabilityProbe("probe", HEALTH_PROBE)
    rules = script(
        answer_rule("Slight rain", "Code 61 is slight rain."),
        call_tool_rule("Trigger boom", "boom", {}),
        call_tool_rule(CODE_ASK, "weather_code_meaning", {"code": 61}),
    )
    with harness(plugins=[_boomer(), weather_plugin(), probe.plugin()], fake_model=rules) as h:
        broken = h.chat("Trigger boom")
        assert broken.answered and broken.error is None
        assert "boom is unavailable" in broken.text and "RuntimeError" in broken.text
        rec = record(h, "boomer")
        assert rec.status.value == "degraded" and rec.failure_count >= 1
        assert "tool:boom: RuntimeError: kaboom" in (rec.last_error or "")
        # the call was audited before and after, even though the tool raised
        assert {"pre_tool_use", "post_tool_use"} <= hooks_for(h, "boom")
        # a sibling plugin is untouched, on the next turn of the same runtime
        assert record(h, "weather-now").failure_count == 0
        fine = h.chat(CODE_ASK)
        assert fine.text == "Code 61 is slight rain."
        line = health_line(health_text(h, probe), "boomer")
        assert "[yellow]" in line and "kaboom" in line, line
        assert h.audit_gaps() == []


def test_a_plugin_whose_setup_raises_is_failed_and_the_others_still_mount() -> None:
    def setup(api: PluginAPI) -> None:
        raise RuntimeError("dead on arrival")

    probe = CapabilityProbe("probe", HEALTH_PROBE)
    with harness(
        plugins=[plugin(setup, manifest={"name": "dead"}), weather_plugin(), probe.plugin()]
    ) as h:
        assert h.plugins()["dead"] == ("failed", "setup failed: RuntimeError: dead on arrival")
        assert h.plugin_loaded("weather-now") and h.plugin_loaded("system")
        line = health_line(health_text(h, probe), "dead")
        assert "[red]" in line and "dead on arrival" in line, line


def test_a_provider_that_raises_is_charged_to_the_provider_and_the_consumer_turn_survives() -> None:
    """The consumer (the REAL trip-advisory) only catches ``CapabilityUnavailable``. A provider
    that raises something else must still not take the turn down, and the failure must be
    charged to the provider, not to the consumer."""
    fake = FakeWeather()
    fake.fail = RuntimeError("provider exploded")
    rules = script(
        call_tool_rule(
            "Plan my trip to Lisbon",
            "trip_advisory",
            {"location": "Lisbon", "start": str(day(1)), "end": str(day(2))},
        ),
    )
    with harness(plugins=[provider_plugin(fake), trip_plugin()], fake_model=rules) as h:
        result = h.chat("Plan my trip to Lisbon")
        assert result.answered, result.error
        assert fake.calls, "the provider was reached"
        assert record(h, "fake-weather").failure_count >= 1
        assert "provider exploded" in (record(h, "fake-weather").last_error or "")


@pytest.mark.xfail(
    strict=True,
    reason="GAP (iris-plugin-trip-advisory): the tool catches only CapabilityUnavailable, so any "
    "other exception a provider raises propagates through the consumer's tool and the consumer "
    "is charged (degraded) and the model gets an error instead of the degraded advice",
)
def test_gap_a_provider_failure_is_not_charged_to_the_consumer_too() -> None:
    fake = FakeWeather()
    fake.fail = RuntimeError("provider exploded")
    rules = script(
        call_tool_rule(
            "Plan my trip to Lisbon",
            "trip_advisory",
            {"location": "Lisbon", "start": str(day(1)), "end": str(day(2))},
        ),
    )
    with harness(plugins=[provider_plugin(fake), trip_plugin()], fake_model=rules) as h:
        h.chat("Plan my trip to Lisbon")
        assert record(h, "trip-advisory").failure_count == 0


def test_a_real_provider_and_consumer_survive_a_dead_third_plugin() -> None:
    def setup(api: PluginAPI) -> None:
        raise RuntimeError("dead on arrival")

    meteo = OpenMeteo()
    plugins = [
        plugin(setup, manifest={"name": "dead"}),
        trip_plugin(),
        weather_plugin(meteo.transport()),
    ]
    rules = script(
        answer_rule("Trip to Lisbon", "Pack light."),
        call_tool_rule(
            "Plan my trip to Lisbon",
            "trip_advisory",
            {"location": "Lisbon", "start": str(day(1)), "end": str(day(2))},
        ),
    )
    with harness(plugins=plugins, fake_model=rules) as h:
        assert h.plugin_loaded("weather-now") and h.plugin_loaded("trip-advisory")
        assert h.chat("Plan my trip to Lisbon").text == "Pack light."
        assert len(meteo.requests) == 2  # geocode + forecast: the real provider served it
