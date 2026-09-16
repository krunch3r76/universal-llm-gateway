"""Slice C — hop seal ordering before keystroke (Fable I8)."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

pytestmark = pytest.mark.offline

_SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "liaison-ide-hop.py"
_HOP_ARGS = [
    "liaison-ide-hop.py",
    "--root",
    "10479",
    "--row",
    "R16 test",
    "--transcript-id",
    "94b2a901-7807-460e-8102-2e83bb5b96c7",
    "--no-auto-arm",
    "--dry-run",
]


def _load_hop_module():
    spec = importlib.util.spec_from_file_location("liaison_ide_hop_under_test", _SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_hop_seals_with_channel_hop_before_keystroke(capsys) -> None:
    call_order: list[str] = []
    hop_mod = _load_hop_module()

    def fake_seal(root_id: str, *, transcript_id: str, **kwargs):
        call_order.append("seal")
        assert root_id == "10479"
        assert transcript_id == "94b2a901-7807-460e-8102-2e83bb5b96c7"
        return {"ok": True, "phase": "sealed", "bus_turn": 2621}

    def fake_fire(*args, **kwargs):
        call_order.append("fire")
        return {"ok": True, "dry_run": True, "root": "10479"}

    with (
        patch.object(hop_mod, "seal_hop_window", side_effect=fake_seal),
        patch.object(hop_mod, "fire_ide_hop", side_effect=fake_fire),
        patch.object(hop_mod, "hop_qualifies", return_value={"ok": True}),
        patch.object(hop_mod, "live_watcher_labels", return_value=[]),
        patch.object(hop_mod, "tick_register", return_value="attended"),
        patch.object(hop_mod, "policy_gui_host", return_value="jupiter"),
        patch.object(hop_mod, "build_ide_hop_message", return_value="resume 10479\n"),
        patch.object(sys, "argv", _HOP_ARGS),
    ):
        code = hop_mod.main()
    assert code == 0
    assert call_order == ["seal", "fire"]


def test_hop_aborts_when_seal_fails(capsys) -> None:
    fire_called = False
    hop_mod = _load_hop_module()

    def fake_fire(*args, **kwargs):
        nonlocal fire_called
        fire_called = True
        return {"ok": True}

    with (
        patch.object(
            hop_mod,
            "seal_hop_window",
            return_value={"ok": False, "phase": "seal_timeout"},
        ),
        patch.object(hop_mod, "fire_ide_hop", side_effect=fake_fire),
        patch.object(hop_mod, "hop_qualifies", return_value={"ok": True}),
        patch.object(hop_mod, "live_watcher_labels", return_value=[]),
        patch.object(sys, "argv", _HOP_ARGS),
    ):
        code = hop_mod.main()
    assert code == 2
    assert fire_called is False
    payload = json.loads(capsys.readouterr().out)
    assert payload["phase"] == "seal_timeout"


def test_hop_refuses_without_transcript_id(capsys) -> None:
    fire_called = False
    hop_mod = _load_hop_module()
    args = [
        "liaison-ide-hop.py",
        "--root",
        "10479",
        "--row",
        "R16 test",
        "--no-auto-arm",
    ]

    def fake_fire(*args, **kwargs):
        nonlocal fire_called
        fire_called = True
        return {"ok": True}

    with (
        patch.object(hop_mod, "fire_ide_hop", side_effect=fake_fire),
        patch.object(hop_mod, "seal_hop_window") as seal_mock,
        patch.object(hop_mod, "hop_qualifies", return_value={"ok": True}),
        patch.object(hop_mod, "live_watcher_labels", return_value=[]),
        patch.object(sys, "argv", args),
    ):
        code = hop_mod.main()
    assert code == 2
    assert fire_called is False
    seal_mock.assert_not_called()
    payload = json.loads(capsys.readouterr().out)
    assert payload["phase"] == "seal_transcript_unknown"
