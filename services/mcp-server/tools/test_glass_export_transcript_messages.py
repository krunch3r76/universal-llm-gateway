"""MCP registration and import fence for glass_export_transcript_messages."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from types import ModuleType
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


def test_mcp_handler_uses_stamped_caller_not_body() -> None:
    rec = _Recorder()
    register_glass_export_transcript_messages_tool(rec)  # type: ignore[arg-type]
    _, handler = next(
        t for t in rec.tools if t[0] == "glass_export_transcript_messages"
    )
    root = Path(__file__).resolve().parents[3]
    jsonl_root = root / "tmp_glass_export_mcp"
    tid = "aaaaaaaa-bbbb-cccc-dddd-000000000001"
    path = jsonl_root / tid / f"{tid}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    rec_min = {
        "role": "user",
        "message": {"content": [{"type": "text", "text": "x"}]},
    }
    import json

    path.write_text(json.dumps(rec_min) + "\n", encoding="utf-8")
    try:
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
    finally:
        import shutil

        shutil.rmtree(jsonl_root, ignore_errors=True)


def test_registration_module_does_not_import_bridge_send_path() -> None:
    loaded = {
        name
        for name in sys.modules
        if name.startswith("cursor_bridge.bridge_glass_relay")
        or name == "tools.cursor_bridge"
    }
    mod = importlib.import_module("tools.glass_export_transcript_messages")
    assert isinstance(mod, ModuleType)
    assert "tools.cursor_bridge" not in sys.modules or "tools.cursor_bridge" not in (
        set(sys.modules) - loaded
    )
