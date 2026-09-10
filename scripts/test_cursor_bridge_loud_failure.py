"""Unit tests for cursor keystroke bridge loud-failure helpers."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.modules.setdefault("evdev", MagicMock())
sys.modules.setdefault("evdev.ecodes", MagicMock())

_REPO = Path(__file__).resolve().parents[1]
_WATCHER = _REPO / "scripts" / "watch-cursor-bridge-inbox.py"
_KEYSTROKE = _REPO / "scripts" / "cursor_tab_keystroke.py"


def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


watcher = _load_module(_WATCHER, "watch_cursor_bridge_inbox_test")
keystroke = _load_module(_KEYSTROKE, "cursor_tab_keystroke_test")


@pytest.mark.offline
def test_format_ack_includes_stale_reason() -> None:
    text = watcher._format_ack(
        {
            "ok": False,
            "reason": "tab_ready_stale",
            "phase": "preflight",
            "tab_ready_age_s": 601.0,
            "tab_ready_ttl_s": 600,
        },
        prefix="wake turn=15",
    )
    assert "ok=False" in text
    assert "reason=tab_ready_stale" in text
    assert "tab_ready_age_s=601.0" in text


@pytest.mark.offline
def test_format_ack_includes_false_reason() -> None:
    text = watcher._format_ack(
        {"ok": False, "reason": "no_tab_ready", "phase": "preflight"},
        prefix="wake turn=7",
    )
    assert "ok=False" in text
    assert "reason=no_tab_ready" in text


@pytest.mark.offline
def test_verify_focus_mismatch_fails_loud() -> None:
    with patch.object(
        keystroke, "_active_window_title", return_value=("10459 codev - Cursor", "hyprctl")
    ):
        out = keystroke._verify_focus_title("10462 codev")
    assert out["focus_verified"] is False
    assert out["reason"] == "focus_mismatch"


@pytest.mark.offline
def test_verify_focus_skipped_when_no_probe() -> None:
    with patch.object(keystroke, "_active_window_title", return_value=(None, "none")):
        out = keystroke._verify_focus_title("10462 codev")
    assert out["focus_verified"] is None
    assert out["focus_probe"] == "none"


@pytest.mark.offline
def test_paste_refused_when_uinput_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(keystroke, "_UINPUT_ENABLED_RAW", "")
    monkeypatch.setattr(keystroke, "_require_display", lambda: None)
    out = keystroke.paste_message("probe", repo="/tmp", focus_title="", focus_opener="none", input_focus="none", dry_run=False)
    assert out["ok"] is False
    assert out["reason"] == "uinput_disabled"
