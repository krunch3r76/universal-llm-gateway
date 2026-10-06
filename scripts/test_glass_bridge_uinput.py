"""Regression: Glass bridge ctrl+n requires KEY_N on the orchestrator uinput device."""

from __future__ import annotations

import contextlib
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


@pytest.mark.offline
def test_glass_launch_acquires_inhibit_before_uinput(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Route 1: compositor inhibit must start before the first chord."""
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-1")
    mod = _load_orchestrator_keystroke()
    chosen = {"title": "Cursor Agents", "identifier": "abc"}
    order: list[str] = []

    class _FakeUInput:
        def __init__(self, *args, **kwargs):
            order.append("uinput")

        def write(self, *args, **kwargs):
            return None

        def syn(self) -> None:
            return None

        def close(self) -> None:
            return None

    class _FakeProc:
        def poll(self):
            return None

        def wait(self, timeout=None):
            return 0

        def kill(self) -> None:
            return None

    @contextlib.contextmanager
    def _fake_inhibit():
        order.append("inhibit")
        yield {"ok": True, "state": "active", "route": 1, "_proc": _FakeProc()}

    with patch.object(mod, "UInput", _FakeUInput):
        with patch.object(mod, "_compositor_shortcuts_inhibit", _fake_inhibit):
            with patch.object(mod, "_pick_agents_window", return_value=chosen):
                with patch.object(
                    mod,
                    "_focus_window",
                    return_value={"ok": True, "focused": True},
                ):
                    with patch.object(
                        mod, "_require_cursor_keyboard", return_value={"ok": True}
                    ):
                        with patch.object(mod, "_wl_copy", return_value=None):
                            with patch.object(mod, "_new_glass_agent", lambda ui: order.append("chord")):
                                with patch.object(mod, "_paste", lambda ui: None):
                                    with patch.object(mod, "_submit_composer", lambda ui: order.append("submit")):
                                        out = mod.launch_glass_chat_with_message(
                                            "hello", repo=str(_REPO), dry_run=False
                                        )
    assert order.index("inhibit") < order.index("uinput") < order.index("chord")
    assert "submit" in order
    assert out.get("inhibit", {}).get("route") == 1


@pytest.mark.offline
def test_glass_launch_mid_keyboard_fail_skips_submit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-1")
    mod = _load_orchestrator_keystroke()
    chosen = {"title": "Cursor Agents"}
    submit_calls: list[str] = []

    class _FakeProc:
        def poll(self):
            return None

        def wait(self, timeout=None):
            return 0

        def kill(self) -> None:
            return None

    @contextlib.contextmanager
    def _fake_inhibit():
        yield {"ok": True, "_proc": _FakeProc()}

    def _fail_after_ctrl_n(*args, **kwargs):
        if not getattr(_fail_after_ctrl_n, "seen", False):
            _fail_after_ctrl_n.seen = True
            return {"ok": True}
        raise SystemExit(json.dumps({"ok": False, "phase": "focus", "reason": "cursor_not_activated"}))

    _fail_after_ctrl_n.seen = False

    with patch.object(mod, "_compositor_shortcuts_inhibit", _fake_inhibit):
        with patch.object(mod, "_pick_agents_window", return_value=chosen):
            with patch.object(mod, "_focus_window", return_value={"ok": True}):
                with patch.object(mod, "_require_cursor_keyboard", side_effect=_fail_after_ctrl_n):
                    with patch.object(
                        mod,
                        "_ui",
                        return_value=type("UI", (), {"close": lambda self: None})(),
                    ):
                        with patch.object(mod, "_new_glass_agent", lambda ui: None):
                            with patch.object(
                                mod,
                                "_submit_composer",
                                lambda ui: submit_calls.append("submit"),
                            ):
                                with pytest.raises(SystemExit) as caught:
                                    mod.launch_glass_chat_with_message(
                                        "hello", repo=str(_REPO), dry_run=False
                                    )
    assert submit_calls == []
    assert "phase" in str(caught.value) and "focus" in str(caught.value)
