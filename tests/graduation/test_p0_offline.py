"""P0: the FULL test suite of each plugin, run under ``no_network()`` and a fake model.

The suites are run exactly as their own CI runs them, in a subprocess (so their conftest, their
fixtures and their ``tests`` package are theirs), with ``offline_guard`` loaded on top: every
test is wrapped in ``no_network()`` plus a scripted-model-only provider. The assertions are
that nothing failed, nothing was skipped (a skip would hide an online-only test), the guard
really wrapped every test that ran, and no wrapped test tried a socket on its own.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
TRIP_ROOT = HERE.parent.parent
WEATHER_ROOT = Path(__import__("iris_plugin_weather_now").__file__).resolve().parents[2]


def _run_suite(root: Path, *extra: str) -> tuple[str, int]:
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([str(HERE), env.get("PYTHONPATH", "")])
    done = subprocess.run(  # noqa: S603 -- our own interpreter, our own arguments
        [sys.executable, "-m", "pytest", "-q", "-p", "offline_guard", "-p", "no:cacheprovider", *extra],
        cwd=root, env=env, capture_output=True, text=True, timeout=900, check=False,
    )  # fmt: skip
    return done.stdout + done.stderr, done.returncode


def _count(out: str, word: str) -> int:
    found = re.search(rf"(\d+) {word}", out.splitlines()[-1] if word != "tests" else out)
    return int(found.group(1)) if found else 0


@pytest.mark.parametrize(
    ("root", "extra"),
    [
        (WEATHER_ROOT, ()),
        # this directory is the suite under test here; do not run it inside itself
        (TRIP_ROOT, ("--ignore=tests/graduation",)),
    ],
    ids=["weather-now", "trip-advisory"],
)
def test_the_whole_suite_passes_offline_on_a_fake_model(root: Path, extra: tuple[str, ...]) -> None:
    out, code = _run_suite(root, *extra)
    summary = [ln for ln in out.splitlines() if re.search(r"\d+ passed", ln)]
    assert code == 0 and summary, out[-3000:]
    passed = _count(out, "passed")
    assert passed > 0
    assert not re.search(r"\d+ (skipped|failed|error)", summary[-1]), summary[-1]
    guard = re.search(r"OFFLINE_GUARD tests=(\d+) blocked_attempts=(\d+)", out)
    assert guard, out[-1500:]
    assert int(guard.group(1)) == passed  # every test that ran ran inside the guard
    assert int(guard.group(2)) == 0  # and none reached for a socket outside a harness


def test_the_guard_can_fail_a_suite_that_needs_the_network(tmp_path: Path) -> None:
    """Negative control: a test that opens a socket fails under the guard (it is not a no-op)."""
    (tmp_path / "test_online.py").write_text(
        "import socket\n\ndef test_needs_internet():\n"
        "    socket.create_connection(('example.com', 80), timeout=1)\n"
    )
    out, code = _run_suite(tmp_path)
    assert code != 0 and "network access blocked in this test" in out, out[-1500:]
