"""Exclusive Cursor keyboard gate — browser still activated must fail closed."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
_FOCUS = _REPO / "scripts" / "cosmic_focus_window.py"


def _load_focus():
    spec = importlib.util.spec_from_file_location("cosmic_focus_window_test", _FOCUS)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


_SPECIMEN = [
    {
        "handle": 4278190080,
        "identifier": "mgyXFVHSeX5xLwLb6zLcvp1Ml7FXrTuW",
        "title": "krunch3r@orion-node: ~",
        "app_id": "kitty",
        "activated": False,
    },
    {
        "handle": 4278190081,
        "identifier": "nKYa85l4cMNL3WFTAK7xMVuqQHzAPBiL",
        "title": "Phase 1 operator continuity hop - Claude — Mozilla Firefox",
        "app_id": "firefox",
        "activated": True,
    },
    {
        "handle": 4278190082,
        "identifier": "1GTsuA4NedTMQ1fchPkIG5KKoi8cQ82R",
        "title": "Cursor Agents",
        "app_id": "cursor",
        "activated": False,
    },
]


@pytest.mark.offline
def test_firefox_activated_refuses_even_when_cursor_row_exists() -> None:
    exclusive_cursor_keyboard = _load_focus().exclusive_cursor_keyboard
    out = exclusive_cursor_keyboard(
        _SPECIMEN,
        identifier="1GTsuA4NedTMQ1fchPkIG5KKoi8cQ82R",
        title="Cursor Agents",
    )
    assert out["ok"] is False
    assert out["reason"] == "browser_activated"
    assert out["browsers"][0]["app_id"] == "firefox"


@pytest.mark.offline
def test_cursor_and_firefox_both_activated_still_refuses() -> None:
    exclusive_cursor_keyboard = _load_focus().exclusive_cursor_keyboard
    rows = json.loads(json.dumps(_SPECIMEN))
    rows[2]["activated"] = True
    out = exclusive_cursor_keyboard(
        rows,
        identifier="1GTsuA4NedTMQ1fchPkIG5KKoi8cQ82R",
        title="Cursor Agents",
    )
    assert out["ok"] is False
    assert out["reason"] == "browser_activated"


@pytest.mark.offline
def test_exclusive_cursor_agents_passes() -> None:
    exclusive_cursor_keyboard = _load_focus().exclusive_cursor_keyboard
    rows = json.loads(json.dumps(_SPECIMEN))
    rows[1]["activated"] = False
    rows[2]["activated"] = True
    out = exclusive_cursor_keyboard(
        rows,
        identifier="1GTsuA4NedTMQ1fchPkIG5KKoi8cQ82R",
        title="Cursor Agents",
    )
    assert out["ok"] is True
    assert out["cursor"]["title"] == "Cursor Agents"


@pytest.mark.offline
def test_cursor_not_activated_refuses() -> None:
    exclusive_cursor_keyboard = _load_focus().exclusive_cursor_keyboard
    rows = json.loads(json.dumps(_SPECIMEN))
    rows[1]["activated"] = False
    out = exclusive_cursor_keyboard(
        rows,
        identifier="1GTsuA4NedTMQ1fchPkIG5KKoi8cQ82R",
        title="Cursor Agents",
    )
    assert out["ok"] is False
    assert out["reason"] == "cursor_not_activated"
