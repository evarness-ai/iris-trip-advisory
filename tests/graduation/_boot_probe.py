"""Run INSIDE a venv: boot a governed IRIS the way ``iris`` does and report what it sees.

Not a test (leading underscore): ``test_p0_closure`` runs it with the python of a second
venv, before and after ``pip uninstall``, and compares the JSON it prints. The plugins are
found ONLY through their ``iris_harness.plugins`` entry points (``IRIS_PLUGINS_ENABLE``);
nothing is mounted in-process except a probe that can read System Health. Stable tier only.

usage: python _boot_probe.py <home-dir> <enabled,plugins> <ask-message> <start> <end>
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from iris_harness.sdk import PluginAPI
from iris_harness.testing import harness, plugin


def main(home: str, enabled: str, ask: str, start: str, end: str) -> dict:
    api_box: list[PluginAPI] = []
    probe = plugin(
        api_box.append,
        manifest={"name": "probe", "uses": {"tools": ["system_health"]}},
    )
    rules = {
        "rules": [
            {
                "name": "answer",
                "match": {
                    "user": r"(?s)Observation:.*(Trip to Lisbon|Could not check the weather)"
                },
                "reply": {"content": "Thought: Done.\nFinal Answer: advice delivered"},
            },
            {
                "name": "call",
                "match": {"user": f"User: {ask}"},
                "reply": {
                    "content": "Thought: Ask.\nAction: trip_advisory\nAction Input: "
                    + json.dumps({"location": "Lisbon", "start": start, "end": end})
                },
            },
        ]
    }
    Path(home).mkdir(parents=True, exist_ok=True)
    with harness(
        plugins=[probe],
        fake_model=rules,
        home=Path(home),
        env={"IRIS_PLUGINS_ENABLE": enabled},
    ) as h:
        rt = h._runtime
        tools = sorted(i.name for i in rt.tool_service.describe())
        providers = [
            getattr(p, "name", str(p))
            for p in rt.plugin_registry.capability_providers("weather.forecast")
        ]
        health = api_box[0].tools.call("system_health", {}).text
        turn = h.chat(ask)
        observation = h.model_calls()[-1].user.split("Observation:")[-1].strip()[:240]
        tool_rows = sorted({r.tool for r in h.audit_rows() if r.tool})
        out = {
            "plugins": {k: list(v) for k, v in h.plugins().items()},
            "tools": tools,
            "providers": providers,
            "health_plugin_rows": [ln for ln in health.splitlines() if ln.startswith("- plugin:")],
            "answered": turn.answered,
            "answer": turn.text,
            "observation": observation,
            "audited_tools": tool_rows,
            "audit_gaps": h.audit_gaps(),
        }
        home_dir = h.home
    out["home_files"] = (
        sorted(str(p.relative_to(home_dir)) for p in home_dir.rglob("*") if p.is_file())
        if home_dir.exists()
        else []
    )
    return out


if __name__ == "__main__":
    print("PROBE_JSON=" + json.dumps(main(*sys.argv[1:6])))
