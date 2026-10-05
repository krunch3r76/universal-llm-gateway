"""Regression: Glass bridge ctrl+n requires KEY_N on the orchestrator uinput device."""

from __future__ import annotations

import importlib.util
import json
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest

sys.modules.setdefault("evdev", __import__("unittest.mock", fromlist=["MagicMock"]).MagicMock())
_e = sys.modules["evdev.ecodes"] = __import__("types").SimpleNamespace()
_e.EV_KEY = 1
_e.KEY_N = 49
_e.KEY_ESC = 1
_e.KEY_S = 31

_REPO = Path(__file__).resolve().parents[1]
_KEYSTROKE = _REPO / "scripts" / "orchestrator_tab_keystroke.py"


def _load_orchestrator_keystroke():
    spec = importlib.util.spec_from_file_location(
        "orchestrator_tab_keystroke_test", _KEYSTROKE
    )
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.offline
def test_orchestrator_uinput_declares_key_n_for_glass_ctrl_n() -> None:
    """KEY_N is outside ESC..S; without it Glass never receives ctrl+n."""
    mod = _load_orchestrator_keystroke()
    captured: list[dict] = []

    class _FakeUInput:
        def __init__(self, events=None, **kwargs):
            captured.append(events or {})

        def close(self) -> None:
            return None

    with patch.object(mod, "UInput", _FakeUInput):
        with patch.object(time, "sleep", lambda *_a, **_k: None):
            mod._ui()
    assert captured, "UInput was not constructed"
    keys = next(iter(captured[0].values()))
    assert mod.e.KEY_N in keys


@pytest.mark.offline
def test_glass_open_plan_skips_model_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-1")
    mod = _load_orchestrator_keystroke()
    with patch.object(
        mod, "_pick_agents_window", return_value={"title": "Cursor Agents"}
    ):
        out = mod.launch_glass_chat_with_message(
            "hello", repo=str(_REPO), dry_run=True, model_query=""
        )
    assert out["steps"] == ["focus_glass", "ctrl+n", "paste", "ctrl_enter"]


@pytest.mark.offline
def test_glass_open_aborts_before_keys_when_browser_holds_keyboard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Firefox/Cowork still activated ⇒ no uinput, no Ctrl+Enter."""
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-1")
    mod = _load_orchestrator_keystroke()
    chosen = {
        "title": "Cursor Agents",
        "identifier": "1GTsuA4NedTMQ1fchPkIG5KKoi8cQ82R",
    }

    class _FailUInput:
        def __init__(self, *args, **kwargs):
            raise AssertionError("uinput must not open")

        def close(self) -> None:
            return None

    with patch.object(mod, "UInput", _FailUInput):
        with patch.object(mod, "_pick_agents_window", return_value=chosen):
            with patch.object(
                mod,
                "_focus_window",
                return_value={"ok": True, "focused": True, "activated": chosen},
            ):
                with patch.object(
                    mod,
                    "_require_cursor_keyboard",
                    side_effect=SystemExit(
                        json.dumps({"ok": False, "reason": "browser_activated"})
                    ),
                ):
                    with pytest.raises(SystemExit) as caught:
                        mod.launch_glass_chat_with_message(
                            "hello", repo=str(_REPO), dry_run=False
                        )
    assert "browser_activated" in str(caught.value)
