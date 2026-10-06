"""A pytest plugin (``-p offline_guard``) that runs a whole suite offline on a fake model.

Every test of the suite it is loaded into runs inside ``no_network()`` (any outbound socket
connect raises and is counted) and ``use_fake_model`` with an EMPTY script (any model call
that no test scripted fails loudly instead of reaching a model server). It adds nothing to the
suite's own fixtures: a suite that passes under it needed neither network nor a live model.

At the end it prints one line the graduation test parses::

    OFFLINE_GUARD tests=<n> blocked_attempts=<n>
"""

from __future__ import annotations

import pytest
from iris_harness.testing import Script, no_network, use_fake_model

_state = {"tests": 0, "attempts": 0}


@pytest.fixture(autouse=True)
def _offline_on_a_fake_model():
    with no_network() as attempted, use_fake_model(Script(())):
        yield
    _state["tests"] += 1
    _state["attempts"] += len(attempted)


def pytest_terminal_summary(terminalreporter) -> None:
    terminalreporter.write_line(
        f"OFFLINE_GUARD tests={_state['tests']} blocked_attempts={_state['attempts']}"
    )
