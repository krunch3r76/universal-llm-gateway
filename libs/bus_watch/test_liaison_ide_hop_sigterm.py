"""SIGTERM retire path for liaison-ide-hop after land."""

from __future__ import annotations

import importlib.util
import json
import signal
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

pytestmark = pytest.mark.offline

_REPO = Path(__file__).resolve().parents[2]
_HOP_SCRIPT = _REPO / "scripts" / "liaison-ide-hop.py"
_TID = "db36e9f8-e76f-49e7-bd57-c9c4ffa88195"


def _load_hop_module():
    spec = importlib.util.spec_from_file_location("liaison_ide_hop", _HOP_SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_sigterm_runs_retire_when_pending(tmp_path) -> None:
    hop_mod = _load_hop_module()
    retire_calls: list[tuple[str, str]] = []
    pending_log: list[bool] = []

    def fake_retire(root, holder, **_k):
        retire_calls.append((root, holder))
        return {"ok": True, "phase": "retired"}

    def fake_patch(root, *, pending: bool) -> None:
        pending_log.append(pending)

    hop_mod._SIGTERM_RETIRE_CTX.update(
        {
            "root": "10479",
            "transcript_id": _TID,
            "landed": True,
            "retire_pending": True,
        }
    )
    with (
        patch.object(hop_mod, "retire_departing_tab", side_effect=fake_retire),
        patch.object(hop_mod, "_patch_hop_land_retire_pending", side_effect=fake_patch),
        pytest.raises(SystemExit) as exc,
    ):
        hop_mod._sigterm_finish_retire(signal.SIGTERM, None)
    assert exc.value.code == 143
    assert retire_calls == [("10479", f"ide:{_TID}")]
    assert pending_log == [False]


def test_landed_hop_sets_and_clears_retire_pending(tmp_path, capsys) -> None:
    hop_mod = _load_hop_module()
    pending_flags: list[bool] = []

    def track_pending(root, *, pending: bool) -> None:
        pending_flags.append(pending)

    def fake_fire(*_a, **_k):
        return {"ok": True, "phase": "landed", "root": "10479"}

    args = [
        "liaison-ide-hop.py",
        "--root",
        "10479",
        "--row",
        "R16",
        "--transcript-id",
        _TID,
        "--force",
    ]
    with (
        patch.object(hop_mod, "WATCH_DIR", tmp_path),
        patch.object(hop_mod, "harvest_judgment_turns", return_value={"ok": True}),
        patch.object(hop_mod, "load_state", return_value={}),
        patch.object(hop_mod, "effective_policy", return_value={}),
        patch.object(hop_mod, "build_digest", return_value={}),
        patch.object(hop_mod, "resolve_now_row", return_value=("R16", "arg")),
        patch.object(hop_mod, "format_now_line", return_value="R16"),
        patch.object(hop_mod, "seal_hop_window", return_value={"ok": True}),
        patch.object(hop_mod, "fire_ide_hop", side_effect=fake_fire),
        patch.object(hop_mod, "retire_departing_tab", return_value={"ok": True}),
        patch.object(hop_mod, "_patch_hop_land_retire_pending", side_effect=track_pending),
        patch.object(hop_mod, "hop_qualifies", return_value={"ok": True}),
        patch.object(hop_mod, "live_watcher_labels", return_value=[]),
        patch.object(hop_mod, "tick_register", return_value="attended"),
        patch.object(hop_mod, "policy_gui_host", return_value="jupiter"),
        patch.object(hop_mod, "build_ide_hop_message", return_value="resume 10479\n"),
        patch.object(hop_mod, "try_acquire_hop_mutex", return_value={"ok": True}),
        patch.object(hop_mod, "release_hop_mutex", return_value=None),
        patch.object(sys, "argv", args),
    ):
        code = hop_mod.main()
    assert code == 0
    assert pending_flags == [True, False]
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
