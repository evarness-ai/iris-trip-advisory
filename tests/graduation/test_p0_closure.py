"""P0: uninstall + reboot IRIS (closure / removability), in a REAL second virtualenv.

Nothing here is simulated: a fresh venv gets the harness wheel and BOTH plugin wheels,
``pip uninstall`` removes a plugin, and a new interpreter boots a governed IRIS
(``_boot_probe.py``, which finds the plugins only through their entry points) and reports what
it sees. Phases:

* A  both installed            -> the baseline
* B  weather-now uninstalled   -> reboot with the profile STILL naming it (the stale-profile
                                  case) and again with a clean profile
* C  trip-advisory uninstalled -> reboot: both names stale, the core still boots
* D  both reinstalled          -> reboot: identical to A (the removal is reversible)

"No residue" is checked at three levels: the files in site-packages, the interpreter's
import machinery and entry points, and the booted runtime (plugin registry, tool pool,
capability providers, audit rows, files in the home it wrote).

Needs the harness wheel (``IRIS_WHEEL_DIR``, default ``../wheelhouse``) and an index (or pip cache)
for the harness's dependencies and the build backend. A missing wheel FAILS, it does not skip.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
TRIP_ROOT = HERE.parent.parent
WEATHER_ROOT = Path(__import__("iris_plugin_weather_now").__file__).resolve().parents[2]
# The interpreter the test runs under, minus the virtualenv: a venv made from a venv is not a
# second environment, so the second one is made from the base interpreter.
BASE_PYTHON = getattr(sys, "_base_executable", sys.executable)
PROBE = HERE / "_boot_probe.py"
BOTH = "weather-now,trip-advisory"
ASK = "Plan my trip to Lisbon"
# Relative to today: the consumer refuses a trip in the past ("error: the trip is in the past")
# before it asks the weather capability anything, so fixed dates (2026-10-06/07) went stale on the
# day they passed and the capability row this test looks for was never written.
START, END = str(date.today() + timedelta(days=1)), str(date.today() + timedelta(days=2))
WEATHER_DIST, TRIP_DIST = "iris-plugin-weather-now", "iris-plugin-trip-advisory"
WEATHER_MOD, TRIP_MOD = "iris_plugin_weather_now", "iris_plugin_trip_advisory"


def _run(*cmd: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if k in ("PATH", "HOME", "LANG", "TMPDIR")}
    return subprocess.run(  # noqa: S603 -- our own interpreter and arguments
        list(cmd), capture_output=True, text=True, env=env, cwd=cwd, timeout=900, check=False
    )


@dataclass
class Venv:
    root: Path
    work: Path
    wheels: list[Path]

    @property
    def py(self) -> str:
        return str(self.root / "bin" / "python")

    def pip(self, *args: str) -> None:
        done = _run(self.py, "-m", "pip", *args, "--disable-pip-version-check", "-q")
        assert done.returncode == 0, f"pip {' '.join(args)} failed:\n{done.stderr[-2000:]}"

    def site_files(self) -> set[str]:
        site = next((self.root / "lib").glob("python3*/site-packages"))
        return {
            str(p.relative_to(site))
            for p in site.rglob("*")
            if p.is_file() and "__pycache__" not in p.parts
        }

    def boot(self, label: str, enabled: str) -> dict:
        home = self.work / f"home-{label}"
        done = _run(self.py, str(PROBE), str(home), enabled, ASK, START, END)
        lines = [ln for ln in done.stdout.splitlines() if ln.startswith("PROBE_JSON=")]
        assert (
            lines
        ), f"boot {label} printed no report (rc={done.returncode}):\n{done.stderr[-3000:]}"
        return json.loads(lines[-1].removeprefix("PROBE_JSON="))

    def python(self, code: str) -> str:
        done = _run(self.py, "-c", code)
        assert done.returncode == 0, done.stderr[-1500:]
        return done.stdout.strip()


def _harness_wheel() -> Path:
    wheel_dir = Path(os.environ.get("IRIS_WHEEL_DIR", TRIP_ROOT.parent / "wheelhouse"))
    wheels = sorted(wheel_dir.glob("iris_harness-*.whl"), key=lambda p: p.stat().st_mtime)
    assert wheels, f"no iris_harness-*.whl in {wheel_dir} (set IRIS_WHEEL_DIR)"
    return wheels[-1]


@pytest.fixture(scope="module")
def phases(tmp_path_factory: pytest.TempPathFactory) -> dict[str, dict]:
    work = tmp_path_factory.mktemp("closure")
    made = _run(BASE_PYTHON, "-m", "venv", str(work / "venv"))
    assert made.returncode == 0, made.stderr[-2000:]
    wheel_dir = work / "wheels"
    wheel_dir.mkdir()
    built = _run(
        sys.executable, "-m", "pip", "wheel", "--no-deps", "-q", "-w", str(wheel_dir),
        str(WEATHER_ROOT), str(TRIP_ROOT),
    )  # fmt: skip
    assert built.returncode == 0, built.stderr[-2000:]
    plugin_wheels = sorted(wheel_dir.glob("iris_plugin_*.whl"))
    assert len(plugin_wheels) == 2, plugin_wheels
    env = Venv(work / "venv", work, plugin_wheels)

    out: dict[str, dict] = {}
    env.pip("install", str(_harness_wheel()), *map(str, plugin_wheels))
    out["A"] = {"site": env.site_files(), "boot": env.boot("A", BOTH)}
    out["A"]["mods"] = env.python(
        f"import importlib.util as u;print(u.find_spec('{WEATHER_MOD}') is not None,"
        f"u.find_spec('{TRIP_MOD}') is not None)"
    )
    out["A"]["eps"] = env.python(
        "from importlib.metadata import entry_points as e;"
        "print(sorted(x.name for x in e(group='iris_harness.plugins') if x.name in ('weather-now','trip-advisory')))"
    )

    env.pip("uninstall", "-y", WEATHER_DIST)
    out["B"] = {
        "site": env.site_files(),
        "boot_stale": env.boot("B-stale", BOTH),
        "boot_clean": env.boot("B-clean", "trip-advisory"),
        "mods": env.python(
            f"import importlib.util as u;print(u.find_spec('{WEATHER_MOD}') is not None,"
            f"u.find_spec('{TRIP_MOD}') is not None)"
        ),
        "eps": env.python(
            "from importlib.metadata import entry_points as e;"
            "print(sorted(x.name for x in e(group='iris_harness.plugins') if x.name in ('weather-now','trip-advisory')))"
        ),
    }

    env.pip("uninstall", "-y", TRIP_DIST)
    out["C"] = {
        "site": env.site_files(),
        "boot": env.boot("C", BOTH),
        "eps": env.python(
            "from importlib.metadata import entry_points as e;"
            "print(sorted(x.name for x in e(group='iris_harness.plugins') if x.name in ('weather-now','trip-advisory')))"
        ),
    }

    env.pip("install", *map(str, plugin_wheels))
    out["D"] = {"site": env.site_files(), "boot": env.boot("D", BOTH)}
    return out


def _comparable(boot: dict) -> dict:
    """What must be identical between phase A and the reinstalled phase D."""
    keep = ("plugins", "tools", "providers", "audited_tools", "answered", "answer", "audit_gaps")
    return {k: boot[k] for k in keep}


# -- the baseline --------------------------------------------------------------------------
def test_a_both_plugins_are_found_only_through_their_entry_points(phases: dict) -> None:
    boot = phases["A"]["boot"]
    assert phases["A"]["eps"] == "['trip-advisory', 'weather-now']"
    assert boot["plugins"]["weather-now"][0] in ("loaded", "degraded")  # degraded: no network
    assert boot["plugins"]["trip-advisory"] == ["loaded", None]
    assert {"trip_advisory", "weather_code_meaning", "weather_forecast"} <= set(boot["tools"])
    assert boot["providers"] == ["weather-now"]
    assert "capability:weather.forecast.forecast" in boot["audited_tools"]
    assert boot["answered"] and boot["audit_gaps"] == []


# -- removing the provider (phase B) -----------------------------------------------------------
def test_b_pip_uninstall_removes_exactly_the_plugin_files(phases: dict) -> None:
    removed = phases["A"]["site"] - phases["B"]["site"]
    added = phases["B"]["site"] - phases["A"]["site"]
    assert removed and all("iris_plugin_weather_now" in p for p in removed), sorted(removed)
    assert not added, sorted(added)
    assert not any("trip_advisory" in p for p in removed)  # the consumer is untouched


def test_b_the_interpreter_no_longer_sees_the_provider(phases: dict) -> None:
    assert phases["B"]["mods"] == "False True"
    assert phases["B"]["eps"] == "['trip-advisory']"


def test_b_reboot_with_a_stale_profile_names_the_missing_plugin_and_keeps_going(
    phases: dict,
) -> None:
    boot = phases["B"]["boot_stale"]
    status, reason = boot["plugins"]["weather-now"]
    assert status == "failed" and "not found" in reason, boot["plugins"]
    assert any(
        "plugin:weather-now [red]" in row and "not found" in row
        for row in boot["health_plugin_rows"]
    ), boot["health_plugin_rows"]
    # its tools and its capability are gone from the runtime ...
    assert "weather_forecast" not in boot["tools"] and "weather_code_meaning" not in boot["tools"]
    assert boot["providers"] == []
    # ... the consumer is still mounted and works, degraded (uses, not requires)
    assert boot["plugins"]["trip-advisory"] == ["loaded", None]
    assert boot["answered"] and boot["audit_gaps"] == []
    assert "no weather forecast service is installed" in boot["observation"]


def test_b_reboot_with_a_clean_profile_has_no_trace_of_the_provider(phases: dict) -> None:
    boot = phases["B"]["boot_clean"]
    assert "weather-now" not in boot["plugins"]
    # The consumer, still installed, reports its optional weather.forecast capability as missing
    # (degraded) since iris-harness#120; that row names the capability, not the provider.
    leftover = [r for r in boot["health_plugin_rows"] if "weather" in r]
    assert all(
        r.startswith("- plugin:trip-advisory ") and "optional capability weather.forecast" in r
        for r in leftover
    ), leftover
    assert not [t for t in boot["tools"] + boot["audited_tools"] if "weather" in t]
    assert not [f for f in boot["home_files"] if "weather" in f.lower()]
    assert boot["providers"] == []


# -- removing the consumer too (phase C) -------------------------------------------------------
def test_c_with_both_removed_the_core_still_boots(phases: dict) -> None:
    boot = phases["C"]["boot"]
    assert phases["C"]["eps"] == "[]"
    assert boot["plugins"]["system"] == ["loaded", None]
    for name in ("weather-now", "trip-advisory"):
        assert boot["plugins"][name][0] == "failed", boot["plugins"]
    # The core also registers `search_docs` since iris-harness#143.
    assert boot["tools"] == ["search_docs", "system_health"]
    assert boot["providers"] == []
    assert boot["answered"], "a turn that asks for a tool that no longer exists still answers"
    removed = phases["B"]["site"] - phases["C"]["site"]
    assert removed and all("iris_plugin_trip_advisory" in p for p in removed), sorted(removed)


# -- reinstall is the exact inverse (phase D) --------------------------------------------------
def test_d_reinstalling_restores_the_baseline_exactly(phases: dict) -> None:
    assert phases["D"]["site"] == phases["A"]["site"]
    a, d = _comparable(phases["A"]["boot"]), _comparable(phases["D"]["boot"])
    # the provider's degraded/loaded status depends on the (refused) network, so compare it apart
    a["plugins"].pop("weather-now"), d["plugins"].pop("weather-now")
    assert a == d
