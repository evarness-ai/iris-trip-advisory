"""P0: stable-import CI check, a deterministic tool, a network tool, PRE/POST rows.

Each tool is exercised on BOTH turn entry points (``chat`` and ``chat_stream``), because the
REPL and the web use the streamed one, and through the Governance Conformance Suite
(``assert_conformant``), which checks the audit rows, the caller stamp, approval and tool
coverage for every declared tool and capability method.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from iris_harness.testing import (
    assert_conformant,
    check_conformance,
    check_stable_imports,
    harness,
    no_network,
)

from .support import (
    OpenMeteo,
    answer_rule,
    call_tool_rule,
    callers_for,
    day,
    hooks_for,
    raw_rows,
    script,
    weather_plugin,
)

ASK = "What is the weather forecast for Lisbon"
HERE = Path(__file__).resolve().parent
TRIP_ROOT = HERE.parent.parent
WEATHER_ROOT = Path(__import__("iris_plugin_weather_now").__file__).resolve().parents[2]


# -- P0 1: CI runs check_stable_imports() ------------------------------------------------
@pytest.mark.parametrize("root", [TRIP_ROOT, WEATHER_ROOT], ids=["trip-advisory", "weather-now"])
def test_each_plugin_imports_only_the_stable_tier(root: Path) -> None:
    """Static check over source AND tests (this suite included)."""
    violations = check_stable_imports([root / "src", root / "tests"])
    assert violations == [], "\n".join(str(v) for v in violations)


def test_the_stable_import_check_can_fail(tmp_path: Path) -> None:
    """Negative control: a file that reaches into the kernel IS reported."""
    bad = tmp_path / "bad.py"
    bad.write_text("from iris_harness.kernel.governance.audit.log import AuditLog\n")
    violations = check_stable_imports([tmp_path])
    assert [v.path.name for v in violations] == ["bad.py"]


@pytest.mark.parametrize("root", [TRIP_ROOT, WEATHER_ROOT], ids=["trip-advisory", "weather-now"])
def test_ci_actually_runs_the_check(root: Path) -> None:
    """The check is wired into both the hosted workflow and the local gate script that
    stands in for it (hosted CI cannot run here), not merely available."""
    for gate in (root / ".github" / "workflows" / "ci.yml", root / "scripts" / "ci_local.sh"):
        assert "check_stable_imports" in gate.read_text(), gate


# -- P0 2: one deterministic read tool -----------------------------------------------------
@pytest.mark.parametrize("entry", ["chat", "chat_stream"])
def test_deterministic_tool_runs_governed_and_is_repeatable(entry: str) -> None:
    rules = script(
        answer_rule("Slight rain", "Code 61 is slight rain."),
        call_tool_rule("Explain WMO code 61", "weather_code_meaning", {"code": 61}),
    )
    observations = []
    for _ in range(2):
        with harness(plugins=[weather_plugin()], fake_model=rules) as h:
            result = getattr(h, entry)("Explain WMO code 61")
            assert result.answered, result.error
            assert result.text == "Code 61 is slight rain."
            observations.append(h.model_calls()[-1].user.split("Observation:")[-1][:200])
            assert {"pre_tool_use", "post_tool_use"} <= hooks_for(h, "weather_code_meaning")
            assert callers_for(h, "weather_code_meaning") == {"model:system"}
            assert h.audit_gaps() == []
    assert observations[0] == observations[1]  # deterministic: same observation both runs
    assert json.loads(observations[0].split("\n")[0].strip().split("\n")[0]) == {
        "code": 61,
        "conditions": "Slight rain",
        "category": "rain",
    }


# -- P0 3: one network-backed tool, and the proof it left only to its declared hosts -------
@pytest.mark.parametrize("entry", ["chat", "chat_stream"])
def test_network_tool_is_governed_and_only_contacts_open_meteo(entry: str) -> None:
    meteo = OpenMeteo()
    rules = script(
        answer_rule("Forecast for Lisbon", "Sunny, then rain."),
        call_tool_rule(ASK, "weather_forecast", {"location": "Lisbon", "days": 3}),
    )
    with harness(plugins=[weather_plugin(meteo.transport())], fake_model=rules) as h:
        result = getattr(h, entry)(ASK)
        assert result.answered, result.error
        assert "Forecast for Lisbon" in h.model_calls()[-1].user
        assert {"pre_tool_use", "post_tool_use"} <= hooks_for(h, "weather_forecast")
    assert {r.url.host for r in meteo.requests} == {
        "geocoding-api.open-meteo.com",
        "api.open-meteo.com",
    }
    assert all(r.url.scheme == "https" for r in meteo.requests)


def test_network_tool_against_the_real_transport_is_refused_not_crashed() -> None:
    """The entry-point plugin has a real httpx client. Its attempt to leave is refused by
    ``no_network`` (which ``harness`` also applies), the tool answers with an error
    observation instead of raising into the turn, and the only hosts it tried are the ones
    it documents (docs/EGRESS.md of the weather plugin)."""
    rules = script(
        answer_rule("error", "The weather service is unreachable."),
        call_tool_rule(ASK, "weather_forecast", {"location": "Lisbon"}),
    )
    with harness(plugins=[weather_plugin()], fake_model=rules) as h, no_network() as attempts:
        result = h.chat(ASK)
        assert result.answered, result.error
        assert "error" in h.model_calls()[-1].user.lower()
        assert hooks_for(h, "weather_forecast") >= {"pre_tool_use", "post_tool_use"}
        # The governed client (iris-harness#171) resolves the host once and connects to the
        # address it checked, so `no_network` now records resolved IPs, not names. The hosts
        # it tried are read from its own `pre_egress` rows instead.
        tried = {r.egress["host"] for r in h.audit_rows(hook_point="pre_egress")}
    assert attempts, "the plugin should have tried to reach Open-Meteo (and been refused)"
    assert tried and tried <= {"geocoding-api.open-meteo.com", "api.open-meteo.com"}


# -- P0 4: PRE_TOOL_USE and POST_TOOL_USE rows, via the conformance suite --------------------
def test_weather_now_passes_the_governance_conformance_suite() -> None:
    meteo = OpenMeteo()
    assert_conformant(
        weather_plugin(meteo.transport()),
        tools={
            "weather_code_meaning": {"code": 3},
            "weather_forecast": {"location": "Lisbon", "days": 2},
        },
        capabilities={"weather.forecast": {"forecast": {"location": "Lisbon", "days": 2}}},
    )


def test_trip_advisory_passes_the_governance_conformance_suite() -> None:
    from .support import trip_plugin

    assert_conformant(
        trip_plugin(),
        tools={"trip_advisory": {"location": "Lisbon", "start": str(day(1)), "end": str(day(2))}},
    )


def test_the_conformance_suite_reports_an_uncovered_tool() -> None:
    """Negative control: leave a declared tool without an example and it is reported."""
    violations = check_conformance(
        weather_plugin(OpenMeteo().transport()),
        tools={"weather_code_meaning": {"code": 3}},
        capabilities={"weather.forecast": {"forecast": {"location": "Lisbon"}}},
    )
    assert [(v.check, v.subject) for v in violations] == [("coverage", "weather_forecast")]


def test_both_rows_name_the_tool_and_a_stamped_caller() -> None:
    """Read straight from the ledger (not only through the suite): for the model's call the
    caller is ``model:<agent>``; for a capability call it is ``plugin:<consumer>``."""
    from .support import FakeWeather, provider_plugin, trip_plugin

    rules = script(
        answer_rule("Trip to", "Pack light."),
        call_tool_rule(
            "Plan my trip to Lisbon",
            "trip_advisory",
            {"location": "Lisbon", "start": str(day(1)), "end": str(day(2))},
        ),
    )
    with harness(plugins=[provider_plugin(FakeWeather()), trip_plugin()], fake_model=rules) as h:
        assert h.chat("Plan my trip to Lisbon").answered
        cap = "capability:weather.forecast.forecast"
        for tool, caller in (("trip_advisory", "model:system"), (cap, "plugin:trip-advisory")):
            rows = raw_rows(h, tool=tool)
            assert {"pre_tool_use", "post_tool_use"} <= {r["hook"] for r in rows}, tool
            assert callers_for(h, tool) == {caller}, tool
            # The external-content floor (iris-harness#137, default on) adds a `transform`
            # row ("marked untrusted") to a tool whose result is `content: external`; every
            # other row is still an `allow`, and every row still carries the stamped caller.
            assert all(
                r["decision"] == "allow"
                or (r["decision"] == "transform" and r["plugin"] == "external_content_floor")
                for r in rows
            ), tool
        pre = [r for r in raw_rows(h, tool=cap) if r["hook"] == "pre_tool_use"]
        assert {r["payload"]["capability_provider"] for r in pre} == {"fake-weather"}
