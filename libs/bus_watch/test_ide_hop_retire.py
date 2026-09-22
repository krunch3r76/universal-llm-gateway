"""Departing-tab teardown after an attended IDE hop lands (11912 2026-09-21)."""

from __future__ import annotations

import json
import signal
from pathlib import Path

import pytest

from bus_watch.ide_hop_retire import (
    GOAL_RELEASE,
    _is_poller_argv,
    _is_supervise_start,
    _is_supervise_tail,
    _label_from_argv,
    departing_watcher_labels,
    retire_departing_tab,
    stop_departing_pollers,
    stop_departing_tails,
    watcher_argv_from_cmdline,
    write_rebuild_argv,
)

pytestmark = pytest.mark.offline


def test_departing_labels_include_terminal_stems(tmp_path: Path) -> None:
    (tmp_path / "11912-r1-closeout.state.json").write_text(
        json.dumps({"status": "complete", "thread": "11999"}),
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


def test_supervise_start_and_poller_argv() -> None:
    start = [
        "bash",
        "scripts/watch-supervise.sh",
        "start",
        "--label",
        "11912-r1",
        "--",
        "scripts/watch-bus-consult-and-page.py",
        "--thread",
        "11999",
    ]
    assert _is_supervise_start(start) is True
    assert _is_poller_argv(start) is True
    assert _is_poller_argv(
        ["python", "scripts/watch-bus-consult-and-page.py", "--label", "11912-r1"]
    )
    assert _is_poller_argv(
        ["bash", "scripts/watch-supervise.sh", "tail", "--label", "11912-r1"]
    ) is False
    assert watcher_argv_from_cmdline(start) == [
        "scripts/watch-bus-consult-and-page.py",
        "--thread",
        "11999",
    ]


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


def test_stop_departing_pollers_snapshots_argv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    killed: list[tuple[int, int]] = []
    monkeypatch.setattr(
        "bus_watch.ide_hop_retire.os.kill",
        lambda pid, sig: killed.append((pid, sig)),
    )
    monkeypatch.setattr(
        "bus_watch.ide_hop_retire._cmdline",
        lambda pid: [
            "bash",
            "scripts/watch-supervise.sh",
            "start",
            "--label",
            "11912-r1",
            "--",
            "scripts/watch-bus-consult-and-page.py",
            "--thread",
            "11999",
        ]
        if pid == 4242
        else [],
    )
    pids = stop_departing_pollers(
        "11912",
        ["11912-r1"],
        watch_dir=tmp_path,
        pids_for=lambda root, labels, watch_dir: {4242: "11912-r1"},
    )
    assert pids == [4242]
    assert killed == [(4242, signal.SIGTERM)]
    saved = json.loads((tmp_path / "11912-r1.argv.json").read_text(encoding="utf-8"))
    assert saved == ["scripts/watch-bus-consult-and-page.py", "--thread", "11999"]


def test_write_rebuild_argv_strips_supervise_wrapper(tmp_path: Path) -> None:
    path = write_rebuild_argv(
        "11912-r1",
        [
            "bash",
            "scripts/watch-supervise.sh",
            "start",
            "--label",
            "11912-r1",
            "--",
            "python",
            "scripts/watch-bus-consult-and-page.py",
        ],
        tmp_path,
    )
    assert json.loads(path.read_text(encoding="utf-8")) == [
        "python",
        "scripts/watch-bus-consult-and-page.py",
    ]


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
        stop_pollers=lambda root, labels, watch_dir=None: [8],
        release=lambda holder, **kw: released.append((holder, kw)) or {"ok": True},
    )
    assert result["ok"] is True
    assert result["stopped_loops"] == [99]
    assert result["stopped_pollers"] == [8]
    assert result["stopped_tails"] == [7]
    assert result["labels"] == ["11912-r1"]
    assert result["goal"] == GOAL_RELEASE
    assert released == [
        (
            "ide:7484bed2-ae52-436b-b428-b74056887478",
            {"pid": None, "root_id": "11912"},
        )
    ]


def test_retire_skips_release_when_holder_is_not_ide() -> None:
    result = retire_departing_tab(
        "11912",
        "ticker:11912",
        stop_loops=lambda _r: [],
        labels_for=lambda *_a, **_k: [],
        stop_tails=lambda *_a, **_k: [],
        stop_pollers=lambda *_a, **_k: [],
        release=lambda *_a, **_k: {"ok": True},
    )
    assert result["seat_release"] == {"ok": False, "reason": "holder_not_ide"}
