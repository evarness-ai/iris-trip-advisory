"""Optional integration: the REAL weather-now plugin as the provider, found the way IRIS
finds any installed plugin (its ``iris_harness.plugins`` entry point), never imported by name
as a dependency. Skipped when it is not installed. Its network is refused by ``harness``,
so the real provider fails to reach Open-Meteo: the consumer must degrade, not break.
"""

from __future__ import annotations

from importlib import import_module
from importlib.metadata import entry_points
from pathlib import Path

import pytest
from iris_harness.testing import harness, plugin

from .test_plugin import NAME, consumer, script

_found = [e for e in entry_points(group="iris_harness.plugins") if e.name == "weather-now"]


@pytest.mark.skipif(not _found, reason="iris-plugin-weather-now is not installed")
def test_real_provider_is_wired_through_the_capability_and_degrades_without_network() -> None:
    setup = _found[0].load()
    manifest = Path(import_module(setup.__module__).__file__).with_name("manifest.yaml")
    with harness(plugins=[consumer(), plugin(setup, manifest=manifest)], fake_model=script()) as h:
        assert h.plugin_loaded("weather-now") and h.plugin_loaded(NAME), h.plugins()
        result = h.chat("Plan my trip to Lisbon")
        assert result.answered, result.error
        # The call went through the capability to the real provider, which could not reach
        # its service (network refused), so the consumer reported that instead of a forecast.
        assert "Could not check the weather" in h.model_calls()[-1].user
        assert any("weather.forecast" in str(r) for r in h.audit_rows(hook_point="pre_tool_use"))
