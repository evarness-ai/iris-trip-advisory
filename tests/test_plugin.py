"""Trip Advisory's tests.

* the advice logic alone;
* both plugins mounted in a governed IRIS: the forecast arrives through the declared
  ``weather.forecast`` capability, in either mount order, on ``chat`` and ``chat_stream``;
* degradation: ``uses`` with no provider degrades; ``requires`` with no provider refuses to
  mount and says why in health.

The model is scripted and the network is refused (``harness`` does both).
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
import yaml
from iris_harness.testing import harness, no_network, plugin

from iris_plugin_trip_advisory import plugin as this_plugin

from .conftest import FakeWeather, day, provider_plugin

NAME = "trip-advisory"
MANIFEST = Path(this_plugin.__file__).with_name("manifest.yaml")
START, END = day(1), day(2)


def consumer(manifest: dict | None = None):
    return plugin(this_plugin.setup, manifest=manifest or MANIFEST)


def requiring_manifest() -> dict:
    raw = deepcopy(yaml.safe_load(MANIFEST.read_text()))
    raw["capabilities"] = {"uses": [], "requires": ["weather.forecast"]}
    return raw


def script() -> dict:
    return {
        "rules": [
            {
                "name": "answer from the advice",
                "match": {"user": r"(?s)Observation:.*Trip to Lisbon"},
                "reply": {"content": "Thought: Done.\nFinal Answer: Pack for rain on day two."},
            },
            {
                "name": "call the tool",
                "match": {"user": "User: Plan my trip to Lisbon"},
                "reply": {
                    "content": "Thought: Ask the advisor.\nAction: trip_advisory\n"
                    f'Action Input: {{"location": "Lisbon", "start": "{START}", "end": "{END}"}}'
                },
            },
        ]
    }


# -- the advice, alone -------------------------------------------------------------------


def test_manifest_declares_the_capability_use_and_no_provision() -> None:
    caps = yaml.safe_load(MANIFEST.read_text())["capabilities"]
    assert caps["uses"] == ["weather.forecast"]
    assert not caps.get("provides")


@pytest.mark.parametrize(
    "bad",
    [
        {},
        {"location": "Lisbon"},
        {"location": "Lisbon", "start": "soon", "end": "later"},
        {"location": "Lisbon", "start": str(END), "end": str(START)},
        {"location": "", "start": str(START), "end": str(END)},
    ],
)
def test_bad_arguments_are_an_observation(bad: dict) -> None:
    assert this_plugin.make_tool(None)(bad).startswith("error:")


# -- collaboration through the declared capability -----------------------------------------


@pytest.mark.parametrize("order", ["provider-first", "consumer-first"])
@pytest.mark.parametrize("entry", ["chat", "chat_stream"])
def test_the_forecast_reaches_the_consumer_only_through_the_capability(
    entry: str, order: str, fake_weather: FakeWeather
) -> None:
    plugins = [provider_plugin(fake_weather), consumer()]
    if order == "consumer-first":
        plugins.reverse()  # providers still mount before consumers
    with harness(plugins=plugins, fake_model=script()) as h:
        assert h.plugin_loaded("fake-weather") and h.plugin_loaded(NAME), h.plugins()
        result = (h.chat if entry == "chat" else h.chat_stream)("Plan my trip to Lisbon")
        assert result.answered, result.error
        assert result.text == "Pack for rain on day two."
        # The provider was reached, with the location and a window covering the trip.
        assert fake_weather.calls == [("Lisbon", 3)]
        # The tool's observation is derived from the provider's data.
        observation = h.model_calls()[-1].user
        assert "Trip to Lisbon, Portugal" in observation
        assert "rain jacket" in observation and "sunscreen" in observation
        assert f"Good for outdoor plans: {START}" in observation
        assert f"Better indoors (rain or wind): {END}" in observation
        # The capability call itself was governed and audited, attributed to the consumer.
        rows = h.audit_rows(hook_point="pre_tool_use")
        assert any("weather.forecast" in str(r) for r in rows), rows
        assert h.audit_gaps() == []


def test_a_provider_failure_degrades_the_answer_not_the_turn() -> None:
    down = FakeWeather(fail=True)
    with harness(plugins=[provider_plugin(down), consumer()], fake_model=script()) as h:
        result = h.chat("Plan my trip to Lisbon")
        assert result.answered, result.error
        assert "Could not check the weather (weather service is down)" in h.model_calls()[-1].user


# -- degradation when the provider is absent --------------------------------------------------


def test_uses_without_a_provider_still_mounts_and_degrades() -> None:
    with harness(plugins=[consumer()], fake_model=script()) as h:
        assert h.plugin_loaded(NAME), h.plugins()[NAME]
        result = h.chat("Plan my trip to Lisbon")
        assert result.answered, result.error
        assert "no weather forecast service is installed" in h.model_calls()[-1].user


def test_requires_without_a_provider_refuses_to_mount_with_a_useful_reason() -> None:
    with harness(plugins=[consumer(requiring_manifest())]) as h:
        assert not h.plugin_loaded(NAME)
        health = h.plugins()[NAME]
        assert "required capability not provided: weather.forecast" in str(health)


def test_requires_with_a_provider_mounts() -> None:
    with harness(plugins=[provider_plugin(FakeWeather()), consumer(requiring_manifest())]) as h:
        assert h.plugin_loaded(NAME), h.plugins()[NAME]


def test_an_undeclared_capability_is_refused_and_the_plugin_shows_degraded() -> None:
    """Asking for a capability the manifest does not list is refused (see GAP-3: the health
    row says ``degraded`` but carries no reason)."""
    raw = yaml.safe_load(MANIFEST.read_text())
    raw["capabilities"] = {}
    fake = FakeWeather()
    with harness(plugins=[provider_plugin(fake), consumer(raw)], fake_model=script()) as h:
        assert h.plugins()[NAME][0] == "degraded"
        h.chat("Plan my trip to Lisbon")
        assert fake.calls == []  # never reached the provider
        assert "no weather forecast service is installed" in h.model_calls()[-1].user


def test_no_socket_is_opened_by_the_consumer() -> None:
    with no_network() as attempts:
        out = this_plugin.make_tool(None)(
            {"location": "Lisbon", "start": str(START), "end": str(END)}
        )
    assert attempts == [] and "Could not check the weather" in out


def test_governance_conformance_on_the_degraded_path() -> None:
    """The harness's reusable suite, run with no provider mounted (the capability is a
    ``uses``, so the tool must still be callable and audited)."""
    from iris_harness.testing import assert_conformant

    assert_conformant(
        consumer(),
        tools={"trip_advisory": {"location": "Lisbon", "start": str(START), "end": str(END)}},
    )


async def test_the_sync_tool_also_works_when_called_inside_a_running_loop() -> None:
    """The loop runs tools on a worker thread today (no running loop); if that ever changes,
    the bridge from sync tool to async capability must still work (see GAP-1)."""
    tool = this_plugin.make_tool(FakeWeather())
    out = tool({"location": "Lisbon", "start": str(START), "end": str(END)})
    assert out.startswith("Trip to Lisbon, Portugal")
