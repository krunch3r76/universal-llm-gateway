"""Unit tests for assistant archive text synthesis (Phase E gap closure)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_STARGATE_PATH = Path(__file__).resolve().parents[1] / "services" / "universal-stargate"
_archive_text_path = (
    _STARGATE_PATH / "systems/pipeline/core/handlers/thread_persistence/archive_text.py"
)
_spec = importlib.util.spec_from_file_location("_tp_archive_text", _archive_text_path)
_mod = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
sys.modules["_tp_archive_text"] = _mod
_spec.loader.exec_module(_mod)

synthesize_assistant_archive_text = _mod.synthesize_assistant_archive_text
is_tool_synthesized_archive_text = _mod.is_tool_synthesized_archive_text


def test_synthesize_preserves_non_empty_content() -> None:
    assert synthesize_assistant_archive_text("Hello", []) == "Hello"


def test_synthesize_from_tool_calls_when_content_empty() -> None:
    tool_calls = [
        {
            "turn": 1,
            "name": "cortex",
            "arguments": {"tool": "search"},
            "result": '{"items": []}',
            "ok": True,
        }
    ]
    text = synthesize_assistant_archive_text("", tool_calls)
    assert is_tool_synthesized_archive_text(text)
    assert "cortex" in text
    assert "turn 1" in text


def test_synthesize_empty_when_no_content_and_no_tools() -> None:
    assert synthesize_assistant_archive_text("", []) == ""
    assert synthesize_assistant_archive_text("   ", []) == ""


def test_synthesize_multiple_tool_calls() -> None:
    tool_calls = [
        {"turn": 1, "name": "cortex", "result": "a", "ok": True},
        {"turn": 2, "name": "rag", "result": "b", "ok": False},
    ]
    text = synthesize_assistant_archive_text("", tool_calls)
    assert "cortex" in text
    assert "rag" in text
    assert "fail" in text
