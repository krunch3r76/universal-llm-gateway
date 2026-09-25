"""Departing-tab teardown after an attended IDE hop lands (11912 2026-09-21)."""

from __future__ import annotations

import inspect
import json
import signal
from pathlib import Path

import pytest

from bus_watch.ide_hop_retire import (
    GOAL_RELEASE,
    _is_supervise_tail,
    _label_from_argv,
    departing_tail_pids,
    departing_watcher_labels,
    retire_departing_tab,
    stop_departing_tails,
)

pytestmark = pytest.mark.offline


def test_departing_labels_include_terminal_stems(tmp_path: Path) -> None:
    (tmp_path / "11912-r1-closeout.state.json").write_text(
        json.dumps({"status": "complete", "thread": "11999"}),
        encoding="utf-8",
    )
    (tmp_path / "house-11912-a1b2c3.state.json").write_text(
        json.dumps({"status": "polling", "thread": "11912"}),
        encoding="utf-8",
    )
    (tmp_path / "other-root.state.json").write_text(
        json.dumps({"status": "waiting", "thread": "10479"}),
        encoding="utf-8",
    )
    (tmp_path / "foreign.state.json").write_text(
        json.dumps({"status": "waiting", "thread": "11912"}),
        encoding="utf-8",
    )
    assert departing_watcher_labels("11912", tmp_path) == [
        "11912-r1-closeout",
        "foreign",
    ]


def test_supervise_tail_match_skips_forever() -> None:
    tail = ["bash", "scripts/watch-supervise.sh", "tail", "--label", "11912-r1"]
    forever = [*tail, "--forever"]
    assert _is_supervise_tail(tail) is True
    assert _is_supervise_tail(forever) is False
    assert _label_from_argv(tail) == "11912-r1"
    assert _label_from_argv(["watch-supervise.sh", "tail", "--label=11912-r1"]) == (
        "11912-r1"
    )


def test_departing_tail_pids_includes_house_label(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeProc:
        def __init__(self, pid: int, cmdline: list[str]) -> None:
            self.info = {"pid": pid, "cmdline": cmdline}

    house_tail = [
        "bash",
        "scripts/watch-supervise.sh",
        "tail",
        "--label",
        "house-12586-a1b2c3",
    ]
    monkeypatch.setattr(
        "bus_watch.ide_hop_retire.psutil.process_iter",
        lambda attrs: [FakeProc(9001, house_tail)],
    )
    pids = departing_tail_pids("12586", labels=["12586-r1-closeout"])
    assert pids == [9001]


def test_stop_departing_tails_uses_injected_pids(monkeypatch: pytest.MonkeyPatch) -> None:
    killed: list[tuple[int, int]] = []
    monkeypatch.setattr(
        "bus_watch.ide_hop_retire.os.kill",
        lambda pid, sig: killed.append((pid, sig)),
    )
    pids = stop_departing_tails(
        "11912",
        ["11912-r1"],
        pids_for=lambda root, labels: [4242, 4243],
    )
    assert pids == [4242, 4243]
    assert killed == [(4242, signal.SIGTERM), (4243, signal.SIGTERM)]


def test_retire_departing_tab_stops_loops_tails_and_releases(
    tmp_path: Path,
) -> None:
    released: list[tuple] = []
    result = retire_departing_tab(
        "11912",
        "ide:7484bed2-ae52-436b-b428-b74056887478",
        watch_dir=tmp_path,
        stop_loops=lambda root: [99] if root == "11912" else [],
        labels_for=lambda root, watch_dir: ["11912-r1"],
        stop_tails=lambda root, labels: [7] if labels == ["11912-r1"] else [],
        release=lambda holder, **kw: released.append((holder, kw)) or {"ok": True},
    )
    assert result["ok"] is True
    assert result["stopped_loops"] == [99]
    assert result["stopped_tails"] == [7]
    assert result["labels"] == ["11912-r1"]
    assert result["goal"] == GOAL_RELEASE
    assert set(result) == {
        "ok",
        "root",
        "stopped_loops",
        "labels",
        "stopped_tails",
        "stopped_tab_background",
        "goal",
        "seat_release",
    }
    assert released == [
        (
            "ide:7484bed2-ae52-436b-b428-b74056887478",
            {"pid": None, "root_id": "11912"},
        )
    ]
    sig = inspect.signature(retire_departing_tab)
    assert tuple(sig.parameters) == (
        "root",
        "holder",
        "transcript_id",
        "watch_dir",
        "stop_loops",
        "labels_for",
        "stop_tails",
        "release",
    )


def test_retire_skips_release_when_holder_is_not_ide() -> None:
    result = retire_departing_tab(
        "11912",
        "ticker:11912",
        stop_loops=lambda _r: [],
        labels_for=lambda *_a, **_k: [],
        stop_tails=lambda *_a, **_k: [],
        release=lambda *_a, **_k: {"ok": True},
    )
    assert result["seat_release"] == {"ok": False, "reason": "holder_not_ide"}
