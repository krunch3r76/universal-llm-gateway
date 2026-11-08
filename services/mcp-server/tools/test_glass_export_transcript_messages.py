"""MCP registration and import fence for glass_export_transcript_messages."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from request_profile import bind_request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.glass_export_transcript_messages import (  # noqa: E402
    register_glass_export_transcript_messages_tool,
)

pytestmark = pytest.mark.offline


class _Recorder:
    def __init__(self) -> None:
        self.tools: list[tuple[str, Any]] = []

    def tool(self, *, title: str):
        def deco(fn):
            self.tools.append((fn.__name__, fn))
            return fn

        return deco


def test_register_exports_named_tool() -> None:
    rec = _Recorder()
    register_glass_export_transcript_messages_tool(rec)  # type: ignore[arg-type]
    names = [name for name, _ in rec.tools]
    assert "glass_export_transcript_messages" in names


def test_mcp_handler_uses_stamped_caller_not_body(tmp_path: Path) -> None:
    rec = _Recorder()
    register_glass_export_transcript_messages_tool(rec)  # type: ignore[arg-type]
    _, handler = next(
        t for t in rec.tools if t[0] == "glass_export_transcript_messages"
    )
    import json

    jsonl_root = tmp_path / "transcripts"
    tid = "aaaaaaaa-bbbb-cccc-dddd-000000000001"
    path = jsonl_root / tid / f"{tid}.jsonl"
    rec_min = {
        "role": "user",
        "message": {"content": [{"type": "text", "text": "x"}]},
    }
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(rec_min) + "\n", encoding="utf-8")
    with bind_request("default", caller_identity="glass-supervisor"):
        with patch.dict(
            "os.environ",
            {
                "CURSOR_AGENT_TRANSCRIPTS_ROOT": str(jsonl_root),
                "GLASS_EXPORT_ALLOWED_AGENTS": "glass-supervisor",
            },
        ):
            out = handler(
                transcript_id=tid,
                supervisor_work_key="todo:grok-workflow-glass-supervisor",
            )
    assert out["ok"] is True


def test_registration_module_does_not_import_bridge_send_path() -> None:
    mcp_tools = Path(__file__).resolve().parent
    mcp_server = mcp_tools.parent
    libs = Path(__file__).resolve().parents[3] / "libs"
    script = f"""
import sys
sys.path.insert(0, {str(mcp_server)!r})
sys.path.insert(0, {str(libs)!r})
import tools.glass_export_transcript_messages  # noqa: F401
assert "tools.cursor_bridge" not in sys.modules
assert "cursor_bridge.bridge_glass_relay" not in sys.modules
"""
    proc = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout
