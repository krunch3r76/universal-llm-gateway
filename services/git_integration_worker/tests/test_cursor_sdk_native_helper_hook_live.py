"""Live hook script probes for Task deny and team_dispatch pass-through."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[3]
_HOOK = _REPO / "scripts" / "cursor_sdk_native_helper_hook.py"


def _run_hook(payload: dict) -> dict:
    proc = subprocess.run(
        [sys.executable, str(_HOOK)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        check=False,
        cwd=str(_REPO),
    )
    text = proc.stdout or ""
    for line in reversed(text.splitlines()):
        line = line.strip()
        if line.startswith("{"):
            return json.loads(line)
    return json.loads(text or "{}")


@pytest.fixture()
def enforced_policy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    policy = tmp_path / ".cursor" / "native_helper_policy.json"
    policy.parent.mkdir(parents=True)
    policy.write_text(
        json.dumps({"allow_kinds": ["explore"], "enforcement": "enforced"}),
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(tmp_path))


def test_task_general_purpose_denied_pre_start(enforced_policy: None) -> None:
    out = _run_hook(
        {
            "tool_name": "Task",
            "tool_input": {"subagent_type": "generalPurpose"},
        }
    )
    assert out.get("permission") == "deny"


def test_team_dispatch_unmatched_by_native_helper_hook(enforced_policy: None) -> None:
    out = _run_hook(
        {
            "tool_name": "MCP",
            "tool_input": {
                "tool": "team_dispatch",
                "arguments": '{"op": "generate"}',
            },
        }
    )
    assert out.get("permission") == "allow"


def test_ordinary_tools_and_allowed_explore(enforced_policy: None) -> None:
    explore = _run_hook(
        {
            "tool_name": "Task",
            "tool_input": {"subagent_type": "explore"},
        }
    )
    assert explore.get("permission") == "allow"
    shell = _run_hook({"tool_name": "Shell", "tool_input": {}})
    assert shell.get("permission") == "allow"