"""IDE follow-up keystroke (R10a): paste induction into lock-holder tab."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from bus_watch.ide_followup import fire_ide_followup, remote_followup_command
from bus_watch.ide_hop_landing import (
    induction_head_line,
    transcript_byte_size,
    wait_for_induction_landed,
)

pytestmark = pytest.mark.offline


def test_remote_followup_command_locks_compositor_activate() -> None:
    cmd = remote_followup_command(
        "/repo/tmp/watchers/handoff-messages/m.md",
        remote_repo="/repo",
        focus_title="Cursor Agents",
    )
    assert " followup " in cmd
    assert "--no-raise --focus-title 'Cursor Agents' --focus-app-id cursor" in cmd
    assert " launch " not in cmd
    assert "ctrl_n" not in cmd
    operator = remote_followup_command("/repo/m.md", remote_repo="/repo", no_raise=True)
    assert "--no-raise" in operator
    assert "--focus-title" not in operator


def test_fire_ide_followup_refuses_unset_gui_host() -> None:
    out = fire_ide_followup(
        "WAKE 10479 · turns=1",
        root_id="10479",
        gui_host=None,
        dry_run=True,
    )
    assert out["ok"] is False
    assert out["phase"] == "gui_host_unset"
    assert "Ask the operator which node" in out["fix"]
    assert "Do not default to jupiter" in out["fix"]


def test_fire_ide_followup_refuses_non_ide_holder(tmp_path: Path) -> None:
    lock_path = tmp_path / "liaison-fable-10479.lock"
    lock_path.write_text(
        json.dumps({"holder": "sdk:dispatch-abc", "root": "10479"}),
        encoding="utf-8",
    )
    with patch(
        "bus_watch.ide_followup.read_lock",
        return_value=json.loads(lock_path.read_text()),
    ):
        out = fire_ide_followup(
            "WAKE 10479 · turns=1",
            root_id="10479",
            gui_host="jupiter",
            dry_run=True,
        )
    assert out["ok"] is False
    assert out["phase"] == "not_ide_holder"


def test_fire_ide_followup_dry_run_prints_induction_and_remote_cmd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    lock = {"holder": f"ide:{tid}", "root": "10479"}
    induction = "WAKE 10479 · turns=214 · 2026-09-13T04:10:00Z\nOne step: harvest"
    monkeypatch.setattr("bus_watch.ide_followup.read_lock", lambda _root: lock)
    monkeypatch.setattr(
        "bus_watch.ide_followup.HANDOFF_MSG_DIR",
        tmp_path / "handoff-messages",
    )
    monkeypatch.setattr("bus_watch.ide_followup._REPO", tmp_path)
    out = fire_ide_followup(
        induction,
        root_id="10479",
        gui_host="jupiter",
        dry_run=True,
        remote_repo="/repo",
    )
    assert out["ok"] is True
    assert out["dry_run"] is True
    assert out["induction"] == induction
    assert " followup " in out["remote_cmd"]
    assert out["holder_transcript_id"] == tid


def _write_user_row(path: Path, text: str) -> None:
    row = {"role": "user", "message": {"content": [{"type": "text", "text": text}]}}
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row) + "\n")


def test_induction_landing_waits_for_new_user_row(tmp_path: Path) -> None:
    tid = "bbbbbbbb-cccc-dddd-eeee-ffffffffffff"
    d = tmp_path / tid
    d.mkdir()
    path = d / f"{tid}.jsonl"
    path.write_text('{"role":"user","message":{"content":[{"text":"resume 10479"}]}}\n')
    since = transcript_byte_size(tid, tmp_path)
    marker = induction_head_line("WAKE 10479 · turns=1 · ts")
    assert (
        wait_for_induction_landed(
            tid,
            marker,
            since_bytes=since,
            transcripts_dir=tmp_path,
            timeout_s=0.05,
            poll_s=0.01,
        )
        is False
    )
    _write_user_row(path, f"{marker}\nOne step")
    assert (
        wait_for_induction_landed(
            tid,
            marker,
            since_bytes=since,
            transcripts_dir=tmp_path,
            timeout_s=0.05,
            poll_s=0.01,
        )
        is True
    )


def test_fire_ide_followup_live_mocks_ssh_and_landing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tid = "cccccccc-dddd-eeee-ffff-000000000001"
    lock = {"holder": f"ide:{tid}", "root": "10479"}
    induction = "WAKE 10479 · turns=5 · 2026-09-13T04:10:00Z"
    transcripts = tmp_path / "transcripts"
    d = transcripts / tid
    d.mkdir(parents=True)
    path = d / f"{tid}.jsonl"
    path.write_text('{"role":"user","message":{"content":[{"text":"resume 10479"}]}}\n')
    since = path.stat().st_size

    def _ssh(_cmd: list[str], **_kw):  # noqa: ANN001, ANN202
        _write_user_row(path, induction)
        proc = type("P", (), {})()
        proc.returncode = 0
        proc.stdout = json.dumps({"ok": True, "steps": ["paste", "ctrl_enter"]})
        proc.stderr = ""
        return proc

    monkeypatch.setattr("bus_watch.ide_followup.read_lock", lambda _root: lock)
    monkeypatch.setattr("bus_watch.ide_followup.AGENT_TRANSCRIPTS", transcripts)
    monkeypatch.setattr("bus_watch.ide_followup.HANDOFF_MSG_DIR", tmp_path / "handoff")
    monkeypatch.setattr("bus_watch.ide_followup._REPO", tmp_path)
    monkeypatch.setattr("bus_watch.ide_followup.subprocess.run", _ssh)

    out = fire_ide_followup(
        induction,
        root_id="10479",
        gui_host="jupiter",
        remote_repo="/repo",
    )
    assert out["ok"] is True
    assert out["landed"] is True
    assert out["holder_transcript_id"] == tid
    assert path.stat().st_size > since


def _load_liaison_induce():
    repo = Path(__file__).resolve().parents[2]
    path = repo / "scripts" / "liaison-induce.py"
    spec = importlib.util.spec_from_file_location("liaison_induce", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def test_liaison_induce_refuses_duplicate_fingerprint(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """AC-4: never fires more than once per digest fingerprint."""
    mod = _load_liaison_induce()
    digest = {
        "fingerprint": "fp-unchanged",
        "induction": "WAKE 10479 · turns=1",
    }
    monkeypatch.setattr(
        mod,
        "load_state",
        lambda _p: {"register": "attended", "last_induction_fingerprint": "fp-unchanged"},
    )
    monkeypatch.setattr(mod, "build_digest", lambda *a, **k: digest)
    monkeypatch.setattr(
        mod,
        "fire_ide_followup",
        lambda *a, **k: pytest.fail("fire_ide_followup must not run"),
    )
    monkeypatch.setattr(sys, "argv", ["liaison-induce.py", "--root", "10479", "--dry-run"])
    rc = mod.main()
    captured = capsys.readouterr()
    out = json.loads(captured.out)
    assert rc == 2
    assert out["phase"] == "already_fired"
    assert out["fingerprint"] == "fp-unchanged"
