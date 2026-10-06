#!/usr/bin/env bash
# Local gate. .github/workflows/ci.yml is the hosted equivalent but is manual-only until
# iris-harness is installable from an index (iris-harness#108), so this script IS the gate.
# It runs the same steps as ci.yml, in the same order:
#
#   1. install   throwaway venv -> iris-harness WHEEL -> pip install -e ".[test]" ruff black
#   2. lint      ruff check . ; black --check .
#   3. stable    iris_harness.testing.check_stable_imports([src, tests])
#   4. tests     python -m pytest -q   (no network: HTTP is MockTransport, model is scripted)
#
# Usage:   scripts/ci_local.sh
#
# Environment (all optional):
#   IRIS_HARNESS_CLONE  path to an iris-harness clone to build the wheel from.
#                       Default: ../iris-harness next to this repository.
#   IRIS_WHEEL_DIR      directory holding a prebuilt iris_harness-*.whl, used when the
#                       clone is absent (or IRIS_USE_WHEEL=1). Default: ../wheelhouse.
#   IRIS_USE_WHEEL=1    skip the build and install the newest wheel in IRIS_WHEEL_DIR.
#   PYTHON              interpreter for the venv. Default: python3.12.
#
# WHERE THIS IS NOT THE HOSTED CI, honestly:
#   1. ci.yml clones iris-harness at IRIS_HARNESS_REF (main) from GitHub; this
#      script builds from your LOCAL clone's working tree (whatever branch/edits it has) or
#      from a prebuilt wheel. Check the clone is at the ref you mean to test against.
#   2. ci.yml runs on ubuntu-latest with a fresh checkout; this runs on your machine, in
#      your working tree (untracked files are linted and tested too).
#   3. ci.yml's pip resolves the latest ruff/black/pip at run time and so does this; there
#      is no lock file in either, so a tool release can change the result in both.
#   4. "No network" is enforced by the harness test fixtures (it refuses sockets), the same
#      as in ci.yml; this script adds no extra network sandbox.
# Bash 3.2 compatible (macOS /bin/bash).

set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

CLONE="${IRIS_HARNESS_CLONE:-$ROOT/../iris-harness}"
WHEEL_DIR="${IRIS_WHEEL_DIR:-$ROOT/../wheelhouse}"
PYTHON="${PYTHON:-python3.12}"

WORK="$(mktemp -d "${TMPDIR:-/tmp}/ci_local.XXXXXX")"
trap 'rm -rf "$WORK"' EXIT
VENV="$WORK/venv"
BUILT="$WORK/wheelhouse"

step() { printf '\n==> %s\n' "$*"; }
die() { echo "ci_local: $*" >&2; exit 1; }

command -v "$PYTHON" >/dev/null 2>&1 || die "$PYTHON not found (set PYTHON=...)"

step "venv ($WORK, removed on exit)"
"$PYTHON" -m venv "$VENV" || die "venv creation failed"
"$VENV/bin/pip" install -q --upgrade pip build || die "pip/build install failed"

if [ "${IRIS_USE_WHEEL:-0}" != "1" ] && [ -f "$CLONE/pyproject.toml" ]; then
  step "build iris-harness wheel from $CLONE"
  "$VENV/bin/python" -m build --wheel "$CLONE" --outdir "$BUILT" >/dev/null \
    || die "wheel build failed"
  WHEEL_SRC="$BUILT"
else
  step "use prebuilt wheel from $WHEEL_DIR"
  WHEEL_SRC="$WHEEL_DIR"
fi
WHEEL="$(ls -t "$WHEEL_SRC"/iris_harness-*.whl 2>/dev/null | head -1)"
[ -n "$WHEEL" ] || die "no iris_harness-*.whl in $WHEEL_SRC (set IRIS_HARNESS_CLONE or IRIS_WHEEL_DIR)"

step "install $(basename "$WHEEL") + plugin"
"$VENV/bin/pip" install -q "$WHEEL" || die "harness wheel install failed"
"$VENV/bin/pip" install -q -e ".[test]" ruff black || die "plugin install failed"
# tests/graduation (issue #81) runs both plugins in one environment: install the sibling
# weather plugin when it is checked out next to this repository (else that suite is not collected).
WEATHER="${IRIS_WEATHER_PLUGIN:-$ROOT/../iris-weather-plugin}"
if [ -f "$WEATHER/pyproject.toml" ]; then
  "$VENV/bin/pip" install -q -e "$WEATHER" || die "weather plugin install failed"
fi

step "lint: ruff"
"$VENV/bin/ruff" check . || die "ruff failed"
step "lint: black --check"
"$VENV/bin/black" --check . || die "black failed"

step "only the stable tier is imported (check_stable_imports)"
"$VENV/bin/python" - <<'PY' || die "check_stable_imports reported violations"
import sys
from pathlib import Path
from iris_harness.testing import check_stable_imports

violations = check_stable_imports([Path("src"), Path("tests")])
for v in violations:
    print(v)
sys.exit(1 if violations else 0)
PY

step "tests (no network, scripted model)"
"$VENV/bin/python" -m pytest -q || die "pytest failed"

printf '\nci_local: all steps passed\n'
