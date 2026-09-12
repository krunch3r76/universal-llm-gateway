"""Title-addressed focus + landing proof for the attended IDE hop (hop 16, 2026-09-12 06:00Z)."""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from bus_watch.ide_hop import build_ide_hop_message, remote_launch_command
from bus_watch.ide_hop_landing import (
    focus_title_for,
    hop_header_line,
    ssh_host_name_from_uri,
    wait_for_landed_transcript,
)

pytestmark = pytest.mark.offline

_HEX_IO = "7b22686f73744e616d65223a22696f227d"  # {"hostName":"io"}


def test_ssh_host_from_hex_authority() -> None:
    uri = f"vscode-remote://ssh-remote%2B{_HEX_IO}/mnt/torus/projects/universal-llm-gateway"
    assert ssh_host_name_from_uri(uri) == "io"


def test_ssh_host_from_bare_authority_and_non_remote() -> None:
    assert ssh_host_name_from_uri("vscode-remote://ssh-remote+io/mnt/x") == "io"
    assert ssh_host_name_from_uri("file:///mnt/x") is None
    assert ssh_host_name_from_uri("") is None


def test_focus_title_names_repo_and_ssh_marker() -> None:
    assert (
        focus_title_for("/mnt/torus/projects/universal-llm-gateway", "io")
        == "universal-llm-gateway [SSH: io]"
    )
    assert focus_title_for("/mnt/torus/projects/universal-llm-gateway", None) == (
        "universal-llm-gateway"
    )


def test_hop_header_line_is_the_landing_marker() -> None:
    message = build_ide_hop_message("10479", row="x", arm_labels=[], tip_cp_ordinal=147)
    assert hop_header_line(message).startswith(
        "Liaison IDE hop (attended register) tip_cp=147"
    )


def test_remote_launch_command_prefers_focus_title_over_uri() -> None:
    cmd = remote_launch_command(
        "/repo/tmp/watchers/handoff-messages/m.md",
        remote_repo="/repo",
        palette_query="New Chat",
        raise_uri="vscode-remote://ssh-remote+io/repo",
        focus_title="universal-llm-gateway [SSH: io]",
    )
    assert "--no-raise --focus-title 'universal-llm-gateway [SSH: io]'" in cmd
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
