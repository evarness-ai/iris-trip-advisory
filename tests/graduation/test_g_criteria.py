"""Evidence for the milestone exit criteria that the P0/P1 bullets do not exercise directly
(G1 core purity, G3 egress, G4 isolation limit, G5 tiers, G10 explainability, and the
undocumented behaviour the consumer's sync->async bridge depends on).

Each test either pins what IS the case, or is an ``xfail(strict=True)`` that states what the
criterion needs and the harness does not do yet: when the harness grows it, the strict xfail
turns red and the entry in docs/GRADUATION.md must be re-scored. The G10 audit-row tests were
xfails against earlier harness builds and pass against current iris-harness main.
"""

from __future__ import annotations

import asyncio
from importlib.metadata import distribution
from pathlib import Path

import pytest
from iris_harness.sdk import PluginAPI
from iris_harness.testing import harness, plugin

from .support import (
    CapabilityProbe,
    FakeWeather,
    OpenMeteo,
    answer_rule,
    call_tool_rule,
    day,
    module_of,
    provider_plugin,
    raw_rows,
    script,
    trip_plugin,
    weather_plugin,
)

ASK = "Plan my trip to Lisbon"
ARGS = {"location": "Lisbon", "start": str(day(1)), "end": str(day(2))}
RULES = script(answer_rule("Trip to", "Advice."), call_tool_rule(ASK, "trip_advisory", ARGS))
CAP = "capability:weather.forecast.forecast"


# -- G1: the core knows neither plugin ---------------------------------------------------------------
def test_g1_the_harness_package_names_neither_plugin() -> None:
    """Scan every text file of the INSTALLED harness for either plugin's name or module. The only
    weather vocabulary in the core is the published ``weather.forecast`` capability (a closed
    catalogue by design), which is not a plugin name."""
    root = Path(str(distribution("iris-harness").locate_file("iris_harness")))
    needles = ("weather-now", "weather_now", "trip-advisory", "trip_advisory")
    # `weather-now` is the example name in `iris plugins new --help` and its name check; the
    # `iris_plugin_` prefix the loader/scaffold use is a naming convention, not a plugin.
    examples = {"cli/plugins.py", "cli/plugin_scaffold.py"}
    hits = []
    for path in root.rglob("*"):
        if path.is_file() and path.suffix in {".py", ".yaml", ".yml", ".md", ".json", ".toml"}:
            rel = str(path.relative_to(root))
            text = path.read_text(errors="ignore")
            hits += [f"{rel}: {n}" for n in needles if n in text and rel not in examples]
    assert hits == [], hits


def test_g1_the_core_does_publish_the_weather_forecast_vocabulary() -> None:
    """Recorded, because it bounds the claim: a provider/consumer pair can only collaborate over
    a capability the SDK already publishes, so the weather domain type is core vocabulary."""
    from iris_harness.sdk.capabilities import CAPABILITIES, Forecast  # noqa: F401

    assert "weather.forecast" in CAPABILITIES


# -- the behaviour the trip-advisory bridge depends on (trip GAP-1) --------------------------------------
def test_tools_run_with_no_running_event_loop_which_the_consumer_bridge_relies_on() -> None:
    seen: list[bool] = []

    def setup(api: PluginAPI) -> None:
        def probe(args: dict) -> str:
            try:
                asyncio.get_running_loop()
                seen.append(True)
            except RuntimeError:
                seen.append(False)
            return "loop-probe-result-7"

        api.register_tool("loop_probe", "Probe.", probe)

    manifest = {"name": "looper", "provides": ["tool"], "tools": {"loop_probe": {"effect": "read"}}}
    rules = script(
        answer_rule("loop-probe-result-7", "done"),
        call_tool_rule("Explain the loop probe", "loop_probe", {}),
    )
    with harness(plugins=[plugin(setup, manifest=manifest)], fake_model=rules) as h:
        for entry in ("chat", "chat_stream"):
            assert getattr(h, entry)("Explain the loop probe").answered
        assert seen == [False, False]  # chat and chat_stream: undocumented, but consistent


# -- G3: a plugin's own outbound HTTP is not governed ----------------------------------------------------
def test_g3_the_weather_tool_reaches_open_meteo_and_the_ledger_never_names_the_host() -> None:
    meteo = OpenMeteo()
    rules = script(
        answer_rule("Forecast for", "Done."),
        call_tool_rule(
            "What is the weather forecast for Lisbon", "weather_forecast", {"location": "Lisbon"}
        ),
    )
    with harness(plugins=[weather_plugin(meteo.transport())], fake_model=rules) as h:
        assert h.chat("What is the weather forecast for Lisbon").answered
        assert {r.url.host for r in meteo.requests} == {
            "geocoding-api.open-meteo.com",
            "api.open-meteo.com",
        }
        egress = [
            r for r in raw_rows(h, tool="weather_forecast") if r["plugin"] == "network_egress"
        ]
        assert [r["reason"] for r in egress] == ["network_egress: not a network tool"]
        from iris_harness.sdk.audit import AuditLog

        ledger = "\n".join(
            row.payload_json + row.reason
            for row in AuditLog(db_path=h.audit_db).query(since=h._started)
        )
        assert "open-meteo" not in ledger


@pytest.mark.xfail(
    strict=True,
    reason="GAP (core, weather GAP-1): nothing mediates or records a plugin's own outbound HTTP; "
    "the manifest has no egress declaration and the network_egress hook only inspects the tool "
    "names in its default list",
)
def test_gap_g3_a_plugin_network_call_leaves_a_governed_row_naming_the_destination() -> None:
    meteo = OpenMeteo()
    rules = script(
        answer_rule("Forecast for", "Done."),
        call_tool_rule(
            "What is the weather forecast for Lisbon", "weather_forecast", {"location": "Lisbon"}
        ),
    )
    with harness(plugins=[weather_plugin(meteo.transport())], fake_model=rules) as h:
        h.chat("What is the weather forecast for Lisbon")
        from iris_harness.sdk.audit import AuditLog

        ledger = "\n".join(
            row.payload_json + row.reason
            for row in AuditLog(db_path=h.audit_db).query(since=h._started)
        )
        assert "api.open-meteo.com" in ledger


# -- G4: what isolation there is, and the in-process limit ---------------------------------------------------
def test_g4_an_in_process_plugin_can_skip_governance_by_importing_another_plugins_function() -> (
    None
):
    """The documented limit (``trust: in-process`` is a contract, not a sandbox), demonstrated so
    nobody scores G4 as adversarial isolation: calling weather-now's function directly produces
    NO ledger row, where ``api.tools.call`` would have produced the full PRE/POST set."""
    thief = CapabilityProbe("thief")
    with harness(plugins=[weather_plugin(OpenMeteo().transport()), thief.plugin()]) as h:
        direct = module_of("weather-now").code_meaning({"code": 61})
        assert "Slight rain" in direct
        assert raw_rows(h, tool="weather_code_meaning") == []  # ungoverned, unaudited
        assert thief.api.tools.call(
            "weather_code_meaning", {"code": 61}
        ).held  # the sanctioned path says no


# -- G5: tiers and data classes are visible per model call ----------------------------------------------------
def test_g5_every_model_call_is_gated_with_its_tier_and_classification() -> None:
    with harness(plugins=[provider_plugin(FakeWeather()), trip_plugin()], fake_model=RULES) as h:
        assert h.chat(ASK).answered
        gate = [r for r in raw_rows(h, hook="pre_llm_call") if r["plugin"] == "egress_gate"]
        assert gate and all(r["decision"] == "allow" for r in gate)
        assert {r["payload"]["target_tier"] for r in gate} == {"tier_1", "tier_2"}
        assert all(r["payload"]["classification"] == "public" for r in gate)
        assert not [
            r for r in gate if r["payload"]["target_tier"] == "tier_3"
        ]  # nothing left the machine


def test_g5_a_personal_capability_result_makes_the_next_model_call_personal_and_local() -> None:
    owner = "jordan.rivers@example.org"

    def ident(api: PluginAPI) -> None:
        api.register_owner_identity_source(lambda: {"email": [owner]})

    idp = plugin(ident, manifest={"name": "idsrc", "identity": {"provides": ["email"]}})
    fake = FakeWeather(location=f"Lisbon, a trip for {owner}")
    with harness(plugins=[idp, provider_plugin(fake), trip_plugin()], fake_model=RULES) as h:
        assert h.chat(ASK).answered
        steps = [r for r in raw_rows(h, hook="pre_llm_call") if r["plugin"] == "egress_gate"]
        last = steps[-1]  # the call that reads the observation containing the (masked) literal
        assert last["payload"]["classification"] == "personal"
        assert last["payload"]["target_tier"] in ("tier_1", "tier_2")  # a local tier


# -- G10: what the ledger says about one turn ---------------------------------------------------------------
def _turn_rows():
    meteo = OpenMeteo()
    with harness(plugins=[weather_plugin(meteo.transport()), trip_plugin()], fake_model=RULES) as h:
        assert h.chat(ASK).answered
        return raw_rows(h)


def test_g10_caller_decision_and_provider_identity_are_visible() -> None:
    rows = _turn_rows()
    model_call = [
        r
        for r in rows
        if r["payload"].get("tool_name") == "trip_advisory" and r["plugin"] == "tool_policy"
    ]
    cap_call = [
        r for r in rows if r["payload"].get("tool_name") == CAP and r["plugin"] == "tool_policy"
    ]
    assert (
        model_call[0]["payload"]["caller"] == "model:system"
    )  # who asked: the model, as agent `system`
    assert (
        cap_call[0]["payload"]["caller"] == "plugin:trip-advisory"
    )  # who asked: the consumer plugin
    assert cap_call[0]["payload"]["capability_provider"] == "weather-now"  # whose code answered
    assert (
        model_call[0]["decision"] == "allow" and model_call[0]["reason"]
    )  # the decision and its reason
    tiers = {r["tier"] for r in rows if r["hook"] == "pre_llm_call"}
    assert {"tier_1", "tier_2"} <= tiers  # which tier each model call went to


def test_g10_a_tool_row_names_the_plugin_that_owns_the_tool() -> None:
    rows = [r for r in _turn_rows() if r["payload"].get("tool_name") == "trip_advisory"]
    assert any(
        r["payload"].get("plugin") == "trip-advisory"
        or r["payload"].get("tool_plugin") == "trip-advisory"
        for r in rows
    )


def test_g10_every_model_call_row_names_the_model() -> None:
    calls = [
        r for r in _turn_rows() if r["hook"] == "pre_llm_call" and r["plugin"] == "egress_gate"
    ]
    assert calls and all("model" in r["payload"] for r in calls)


def test_g10_the_stable_audit_row_type_carries_the_caller() -> None:
    from iris_harness.testing import TurnAuditRow

    assert "caller" in TurnAuditRow.__dataclass_fields__
