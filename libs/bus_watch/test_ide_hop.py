"""IDE hop defaults (:8 Xvfb) and hop message bindings."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from bus_watch.digest_budget import POLICY_DEFAULTS
from bus_watch.ide_context_hop_monitor import check_cursor_ide_context_hops
from bus_watch.ide_hop import (
    build_ide_hop_message,
    fire_ide_hop,
    policy_gui_host,
    policy_judgment_posture,
)

pytestmark = pytest.mark.offline

_REPO = Path(__file__).resolve().parents[2]
_TID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


def test_policy_defaults_gui_host_is_xvfb_eight() -> None:
    assert POLICY_DEFAULTS["gui_host"] == ":8"


def test_policy_defaults_judgment_posture_is_distributed() -> None:
    assert POLICY_DEFAULTS["judgment_posture"] == "distributed"


def test_build_ide_hop_message_default_includes_argon_confer_line() -> None:
    msg = build_ide_hop_message(
        "18291",
        row="R1 test row",
        arm_labels=[],
        register="attended",
    )
    assert "outsource regular judgment/design/advice" in msg
    assert "cursor/gemini-4-argon" in msg


def test_build_ide_hop_message_in_seat_judgment_override() -> None:
    msg = build_ide_hop_message(
        "18460",
        row="R1 test row",
        arm_labels=[],
        register="attended",
        judgment_posture="in_seat",
    )
    assert "outsource regular judgment" not in msg
    assert "IN-SEAT" in msg
    assert "judgment_posture=in_seat" in msg
    assert "§ Peer-house:" in msg


def test_policy_judgment_posture_defaults_to_distributed_without_tick_state(
    tmp_path: Path,
) -> None:
    with patch("bus_watch.ide_hop.WATCH_DIR", tmp_path):
        assert policy_judgment_posture("18291") == "distributed"


def test_policy_judgment_posture_reads_override_from_tick_file(tmp_path: Path) -> None:
    (tmp_path / "liaison-18460.tick.json").write_text(
        json.dumps({"policy": {"judgment_posture": "in_seat"}}),
        encoding="utf-8",
    )
    with patch("bus_watch.ide_hop.WATCH_DIR", tmp_path):
        assert policy_judgment_posture("18460", watch_dir=tmp_path) == "in_seat"


def test_policy_gui_host_defaults_to_eight_without_tick_state(tmp_path: Path) -> None:
    with patch("bus_watch.ide_hop.WATCH_DIR", tmp_path):
        assert policy_gui_host("18291") == ":8"


def test_build_ide_hop_message_includes_hop_xvfb_line() -> None:
    msg = build_ide_hop_message(
        "18291",
        row="R1 test row",
        arm_labels=[],
        register="attended",
    )
    assert "HOP:" in msg
    assert "--gui-host :8" in msg
    assert 'budget.stop_class == \'CONTEXT_BUDGET\'' in msg
    assert "--root 18291" in msg


def test_fire_ide_hop_defaults_gui_host_to_eight_dry_run() -> None:
    seal = {"ok": True, "bus_turn": 1}
    with patch("bus_watch.ide_hop.durable_write_text"):
        out = fire_ide_hop(
            "resume 18291",
            root_id="18291",
            seal=seal,
            gui_host=None,
            dry_run=True,
        )
    assert out.get("ok") is True
    assert out.get("gui_host") == ":8"


def _load_hop_module(name: str):
    hop_path = _REPO / "scripts" / "liaison-ide-hop.py"
    spec = importlib.util.spec_from_file_location(name, hop_path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_liaison_ide_hop_auto_resolves_transcript_from_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from bus_watch import fable_lock as fl

    watch = tmp_path / "watchers"
    watch.mkdir()
    (watch / "liaison-fable-18291.lock").write_text(
        json.dumps({"holder": f"ide:{_TID}", "root": "18291"}),
        encoding="utf-8",
    )
    (watch / "liaison-18291.tick.json").write_text(
        json.dumps({"register": "attended", "policy": {"now_row": "R1"}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(fl, "WATCH_DIR", watch)

    hop_mod = _load_hop_module("liaison_ide_hop_auto_tid_lock")
    captured: dict[str, str] = {}

    def _fake_seal(root, *, transcript_id, preamble):  # noqa: ANN001
        captured["transcript_id"] = transcript_id
        return {"ok": True, "bus_turn": 1}

    monkeypatch.setattr(hop_mod, "harvest_judgment_turns", lambda *a, **k: {"ok": True})
    monkeypatch.setattr(
        hop_mod,
        "build_digest",
        lambda *a, **k: {"policy": {"now_row": "R1"}},
    )
    monkeypatch.setattr(hop_mod, "resolve_now_row", lambda d: ("R1", "policy"))
    monkeypatch.setattr(
        hop_mod,
        "format_now_line",
        lambda raw, src, digest, **k: str(raw or ""),
    )
    monkeypatch.setattr(hop_mod, "hop_qualifies", lambda **k: {"ok": True})
    monkeypatch.setattr(hop_mod, "seal_hop_window", _fake_seal)
    monkeypatch.setattr(
        hop_mod,
        "fire_ide_hop",
        lambda *a, **k: {"ok": True, "phase": "dry"},
    )
    monkeypatch.setattr(hop_mod, "live_watcher_labels", lambda *a, **k: [])
    monkeypatch.setattr(hop_mod, "tick_register", lambda *a, **k: "attended")
    monkeypatch.setattr(hop_mod, "policy_gui_host", lambda *a, **k: ":8")

    monkeypatch.setattr(
        sys,
        "argv",
        ["liaison-ide-hop.py", "--root", "18291", "--dry-run"],
    )
    assert hop_mod.main() == 0
    assert captured["transcript_id"] == _TID


@patch("bus_watch.ide_context_hop_monitor._run_ide_hop")
@patch("bus_watch.ide_context_hop_monitor._budget_for_root")
def test_check_cursor_ide_context_hops_triggers_liaison_hop(
    mock_budget: MagicMock,
    mock_hop: MagicMock,
) -> None:
    mock_budget.return_value = {
        "stop_class": "CONTEXT_BUDGET",
        "transcript_id": _TID,
    }
    mock_hop.return_value = subprocess.CompletedProcess(
        args=[], returncode=0, stdout='{"ok": true}', stderr=""
    )
    store = MagicMock()
    store.subscriptions.return_value = [
        {
            "surface": "cursor",
            "thread_scope": "18291",
            "display": "8",
            "state": "subscribed",
        }
    ]
    mark: dict[str, float] = {}
    check_cursor_ide_context_hops(store, mark=mark, now=1000.0)
    mock_hop.assert_called_once_with("18291", _TID)
    assert mark.get("check:18291") == 1000.0
