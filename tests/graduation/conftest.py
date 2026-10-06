"""The graduation suite needs BOTH plugins installed in one environment (issue #81).

Without the weather plugin installed, this directory is not collected (the rest of the repo's
suite is unaffected) and the report header says so, instead of failing every module on import.
Install both to run it:  pip install -e ../iris-weather-plugin -e ".[test]"
"""

from __future__ import annotations

import importlib.util

BOTH_INSTALLED = importlib.util.find_spec("iris_plugin_weather_now") is not None

collect_ignore_glob = [] if BOTH_INSTALLED else ["test_*.py"]


def pytest_report_header(config):
    if BOTH_INSTALLED:
        return "graduation: iris-plugin-weather-now installed, suite collected"
    return "graduation: iris-plugin-weather-now NOT installed, tests/graduation NOT collected"
