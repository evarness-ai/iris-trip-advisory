"""P1: a missing OPTIONAL dependency degrades; a missing REQUIRED one refuses to mount.

The platform has two kinds of dependency and this file proves each:

* a capability under ``uses`` (optional): the plugin mounts, runs degraded, says so;
* anything under ``requires`` (a capability, a package, an environment variable): the plugin is
  NOT mounted, none of its code runs, and System Health names the reason and the fix.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest
import yaml
from iris_harness.sdk import PluginAPI
from iris_harness.testing import assert_conformant, harness, plugin

from .support import (
    HEALTH_PROBE,
    CapabilityProbe,
    FakeWeather,
    answer_rule,
    call_tool_rule,
    day,
    health_line,
    health_text,
    manifest_of,
    provider_plugin,
    script,
    trip_plugin,
)

TRIP_ASK = "Plan my trip to Lisbon"
TRIP_ARGS = {"location": "Lisbon", "start": str(day(1)), "end": str(day(2))}


def trip_manifest(**capabilities: Any) -> dict[str, Any]:
    raw = deepcopy(yaml.safe_load(manifest_of("trip-advisory").read_text()))
    raw["capabilities"] = capabilities
    return raw


# -- P1 1: missing OPTIONAL dependency -> degraded mode --------------------------------------
def test_missing_optional_capability_mounts_and_answers_in_degraded_mode() -> None:
    rules = script(
        answer_rule("no weather forecast service is installed", "I could not check the weather."),
        call_tool_rule(TRIP_ASK, "trip_advisory", TRIP_ARGS),
    )
    probe = CapabilityProbe("probe", HEALTH_PROBE)
    with harness(plugins=[trip_plugin(), probe.plugin()], fake_model=rules) as h:
        assert h.plugins()["trip-advisory"] == ("loaded", None)  # mounted, not refused
        result = h.chat(TRIP_ASK)
        assert result.answered and result.text == "I could not check the weather."
        observation = h.model_calls()[-1].user
        assert (
            "Could not check the weather: no weather forecast service is installed" in observation
        )
        # ... and it still gives the owner something useful instead of nothing
        assert "pack light layers and a rain jacket" in observation
        assert h.audit_gaps() == []


def test_degraded_mode_is_still_conformant_and_audited() -> None:
    assert_conformant(trip_plugin(), tools={"trip_advisory": TRIP_ARGS})


def test_a_provider_that_comes_back_ends_the_degraded_mode() -> None:
    """Same consumer, same script; with a provider mounted the answer is a forecast, not the
    degraded advice: the degradation is driven by the dependency, not hard-wired."""
    fake = FakeWeather()
    rules = script(
        answer_rule("Trip to Lisbon, Portugal", "Forecast-based advice."),
        call_tool_rule(TRIP_ASK, "trip_advisory", TRIP_ARGS),
    )
    with harness(plugins=[provider_plugin(fake), trip_plugin()], fake_model=rules) as h:
        assert h.chat(TRIP_ASK).text == "Forecast-based advice."
        assert fake.calls


def test_the_manifest_has_no_way_to_call_a_package_optional() -> None:
    """Recorded as a boundary of the contract, not a trip-advisory defect: ``requires.packages``
    is all-or-nothing, so an *optional Python package* has no manifest form; only a capability
    can be optional (``uses``). A plugin that wants one try-imports it and reports nothing."""
    from iris_harness.sdk import PluginManifest

    requires = PluginManifest.model_fields["requires"].annotation.model_fields  # type: ignore[union-attr]
    assert set(requires) == {"python", "packages", "env_vars"}


# -- P1 2: missing REQUIRED dependency -> refusal + a useful health reason ---------------------
def test_missing_required_capability_refuses_to_mount_and_health_says_why() -> None:
    ran: list[str] = []

    def spy(api: PluginAPI) -> None:
        ran.append("setup")

    manifest = trip_manifest(requires=["weather.forecast"])
    probe = CapabilityProbe("probe", HEALTH_PROBE)
    with harness(plugins=[plugin(spy, manifest=manifest), probe.plugin()]) as h:
        status, reason = h.plugins()["trip-advisory"]
        assert status == "failed"
        assert reason == (
            "required capability not provided: weather.forecast " "(no mounted plugin provides it)"
        )
        assert ran == []  # the plugin's setup never ran
        assert "trip_advisory" not in [i.name for i in h._runtime.tool_service.describe()]
        line = health_line(health_text(h, probe), "trip-advisory")
        assert "[red]" in line and "weather.forecast" in line, line
        # it tells the owner what to do next
        assert "plugin:trip-advisory:" in health_text(h, probe) and "iris plugins show" in (
            health_text(h, probe)
        )


def test_the_required_capability_arriving_lets_the_same_plugin_mount() -> None:
    manifest = trip_manifest(requires=["weather.forecast"])
    with harness(
        plugins=[provider_plugin(FakeWeather()), plugin(trip_plugin_setup(), manifest=manifest)]
    ) as h:
        assert h.plugins()["trip-advisory"] == ("loaded", None)


def trip_plugin_setup():
    from .support import entry_point

    return entry_point("trip-advisory").load()


def test_missing_required_package_refuses_to_mount_with_its_name() -> None:
    ran: list[str] = []
    manifest = {"name": "needs-pkg", "requires": {"packages": ["no-such-package-xyz"]}}
    probe = CapabilityProbe("probe", HEALTH_PROBE)
    with harness(
        plugins=[plugin(lambda api: ran.append("setup"), manifest=manifest), probe.plugin()]
    ) as h:
        assert h.plugins()["needs-pkg"] == (
            "failed",
            "required package not installed: no-such-package-xyz",
        )
        assert ran == []
        line = health_line(health_text(h, probe), "needs-pkg")
        assert "[red]" in line and "no-such-package-xyz" in line, line


def test_missing_required_environment_variable_refuses_to_mount_with_its_name() -> None:
    manifest = {"name": "needs-env", "requires": {"env_vars": ["TRIP_TEST_NO_SUCH_VAR"]}}
    with harness(plugins=[plugin(lambda api: None, manifest=manifest)]) as h:
        assert h.plugins()["needs-env"] == (
            "failed",
            "required environment variable unset: TRIP_TEST_NO_SUCH_VAR",
        )
    with harness(
        plugins=[plugin(lambda api: None, manifest=manifest)],
        env={"TRIP_TEST_NO_SUCH_VAR": "set"},
    ) as h:
        assert h.plugins()["needs-env"] == ("loaded", None)


def test_an_undeclared_capability_use_is_refused_and_health_gives_the_reason() -> None:
    """The consumer asks for a capability its manifest does not list: refused, the plugin is
    degraded, and System Health (yellow) says exactly why. The reason is NOT in what
    ``Harness.plugins()`` returns (``("degraded", None)``): that is a gap in the testing helper,
    not in Health, and it supersedes trip-advisory's GAP-3 (see docs/GRADUATION.md)."""
    from .support import record

    fake = FakeWeather()
    probe = CapabilityProbe("probe", HEALTH_PROBE)
    with harness(
        plugins=[
            provider_plugin(fake),
            plugin(trip_plugin_setup(), manifest=trip_manifest()),
            probe.plugin(),
        ]
    ) as h:
        assert h.plugins()["trip-advisory"] == ("degraded", None)
        line = health_line(health_text(h, probe), "trip-advisory")
        assert "[yellow]" in line and "does not declare it under 'capabilities: uses'" in line, line
        assert "does not declare it" in (record(h, "trip-advisory").last_error or "")
        assert fake.calls == []  # the provider was never reached


@pytest.mark.xfail(
    strict=True,
    reason="GAP (core health): a consumer running in degraded mode because an OPTIONAL capability "
    "has no provider is reported green, so the owner cannot see that the plugin is degraded",
)
def test_gap_health_reports_a_missing_optional_capability() -> None:
    probe = CapabilityProbe("probe", HEALTH_PROBE)
    with harness(plugins=[trip_plugin(), probe.plugin()]) as h:
        line = health_line(health_text(h, probe), "trip-advisory")
        assert "weather.forecast" in line and "[green]" not in line, line
