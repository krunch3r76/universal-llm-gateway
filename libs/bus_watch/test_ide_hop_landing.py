"""Focus target + landing proof for the attended IDE hop (hop 16, 2026-09-12 06:00–06:20Z)."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from bus_watch.ide_hop import (
    build_ide_hop_message,
    live_watcher_labels,
    remote_launch_command,
)
from bus_watch.ide_hop_landing import (
    AGENTS_WINDOW_TITLE,
    focus_title_for,
    hop_header_line,
    wait_for_landed_transcript,
)

pytestmark = pytest.mark.offline


def test_focus_title_defaults_to_the_agents_window_and_honours_policy() -> None:
    assert focus_title_for() == AGENTS_WINDOW_TITLE == "Cursor Agents"
    assert (
        focus_title_for("universal-llm-gateway [SSH: io]")
        == "universal-llm-gateway [SSH: io]"
    )


def test_hop_header_line_is_the_landing_marker() -> None:
    message = build_ide_hop_message("10479", row="x", arm_labels=[], tip_cp_ordinal=147)
    assert hop_header_line(message).startswith(
        "Liaison IDE hop (attended register) tip_cp=147"
    )
    assert "Do not stop. Proceed autonomously until a have-to stop." in message
    assert "Hop after harvest is the rule" in message


def test_remote_launch_command_prefers_verified_focus_over_uri() -> None:
    cmd = remote_launch_command(
        "/repo/tmp/watchers/handoff-messages/m.md",
        remote_repo="/repo",
        palette_query="New Chat",
        raise_uri="vscode-remote://ssh-remote+io/repo",
        focus_title="Cursor Agents",
    )
    assert "--no-raise --focus-title 'Cursor Agents' --focus-app-id cursor" in cmd
    assert "--raise-uri" not in cmd
    without = remote_launch_command(
        "/repo/m.md",
        remote_repo="/repo",
        palette_query="New Chat",
        raise_uri="vscode-remote://x",
    )
    assert "--raise-uri" in without and "--focus-title" not in without


def _write_transcript(root: Path, tid: str, first_line: str, mtime: float) -> None:
    d = root / tid
    d.mkdir()
    p = d / f"{tid}.jsonl"
    p.write_text(first_line + "\n", encoding="utf-8")
    os.utime(p, (mtime, mtime))


def test_landing_requires_a_transcript_newer_than_the_fire(tmp_path: Path) -> None:
    marker = "Liaison IDE hop (attended register) tip_cp=147."
    fired = time.time()
    _write_transcript(tmp_path, "old-tab", f'{{"text": "{marker}"}}', fired - 600)
    assert (
        wait_for_landed_transcript(
            marker,
            since_epoch=fired,
            transcripts_dir=tmp_path,
            timeout_s=0.05,
            poll_s=0.01,
        )
        is None
    )
    _write_transcript(tmp_path, "new-tab", f'{{"text": "{marker}"}}', fired + 1)
    assert (
        wait_for_landed_transcript(
            marker,
            since_epoch=fired,
            transcripts_dir=tmp_path,
            timeout_s=0.05,
            poll_s=0.01,
        )
        == "new-tab"
    )


def test_live_watcher_labels_treats_predicate_unmet_as_live(tmp_path: Path) -> None:
    """CDP consults sit at predicate_unmet until the first qualifying reply (a:33284)."""
    (tmp_path / "10534-cdp.state.json").write_text(
        json.dumps({"status": "predicate_unmet", "thread": "10534"}),
        encoding="utf-8",
    )
    (tmp_path / "10534-run.state.json").write_text(
        json.dumps({"status": "running", "thread": "10534"}),
        encoding="utf-8",
    )
    (tmp_path / "10534-done.state.json").write_text(
        json.dumps({"status": "complete", "thread": "10534"}),
        encoding="utf-8",
    )
    (tmp_path / "other-root.state.json").write_text(
        json.dumps({"status": "predicate_unmet", "thread": "10479"}),
        encoding="utf-8",
    )
    labels = live_watcher_labels("10534", watch_dir=tmp_path)
    assert labels == ["10534-cdp", "10534-run"]
