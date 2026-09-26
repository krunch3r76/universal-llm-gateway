"""Selection file and transcript follow. The projection is not involved."""

from __future__ import annotations

import subprocess

from scripts.model_manager.ui.dispatch_monitor.core.dtos import (
    CdpLegRow,
    SdkDispatchRow,
)
from scripts.model_manager.ui.dispatch_monitor.ulg.tail_follow import (
    FollowState,
    follow_step,
)
from scripts.model_manager.ui.dispatch_monitor.ulg.tail_selection import (
    ensure_prose_pane,
    move_selection,
    read_selection,
    tail_targets,
    write_selection,
)


def test_tail_targets_skip_terminal_and_cdp_without_a_harvest_key() -> None:
    sdk = (
        SdkDispatchRow(dispatch_id="live-sdk"),
        SdkDispatchRow(dispatch_id="done-sdk", terminal_ms=1),
    )
    cdp = (
        CdpLegRow(request_id="req-url", chat_url="https://claude.ai/chat/1"),
        CdpLegRow(request_id="req-reg", registration_id="reg-1"),
        CdpLegRow(request_id="req-bare"),
        CdpLegRow(request_id="req-done", chat_url="https://x", terminal_ms=1),
    )
    targets = tail_targets(sdk, cdp)
    assert [(item.kind, item.key, item.label) for item in targets] == [
        ("cursor-sdk", "live-sdk", "live-sdk"),
        ("cdp", "https://claude.ai/chat/1", "req-url"),
        ("cdp", "reg-1", "req-reg"),
    ]


def test_move_and_write_selection(tmp_path) -> None:
    index, arm = move_selection(0, 3, "down")
    assert (index, arm) == (1, False)
    index, arm = move_selection(index, 3, "enter")
    assert arm is True
    path = tmp_path / "selection.json"
    target = tail_targets((SdkDispatchRow(dispatch_id="d1"),), ())[0]
    write_selection(path, target)
    assert read_selection(path) == {"kind": "cursor-sdk", "key": "d1", "label": "d1"}


def test_follow_step_appends_once_then_respects_cursor() -> None:
    class Port:
        def __init__(self) -> None:
            self.cursors: list[int] = []

        def tail(self, kind: str, key: str, cursor: int) -> dict:
            self.cursors.append(cursor)
            if cursor == 0:
                return {
                    "lines": [{"kind": "assistant", "text": "hello"}],
                    "cursor": 1,
                    "eof": False,
                    "source": "sdk.run_lines",
                }
            return {"lines": [], "cursor": 1, "eof": False, "source": "sdk.run_lines"}

    port = Port()
    state = FollowState()
    selection = {"kind": "cursor-sdk", "key": "d1", "label": "d1"}
    first = follow_step(state, selection, port)
    second = follow_step(state, selection, port)
    assert first == ["--- cursor-sdk d1 ---", "[assistant] hello"]
    assert second == []
    assert port.cursors == [0, 1]
    assert state.cursor == 1


def test_ensure_prose_pane_splits_once(monkeypatch, tmp_path) -> None:
    calls: list[list[str]] = []

    def fake_run(args, **_kwargs):
        calls.append(list(args))
        if args[:2] == ["tmux", "list-panes"]:
            stdout = "" if len(calls) == 1 else "dispatch-prose\n"
            return subprocess.CompletedProcess(args, 0, stdout=stdout, stderr="")
        if args[:2] == ["tmux", "split-window"]:
            return subprocess.CompletedProcess(args, 0, stdout="%12\n", stderr="")
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setenv("TMUX", "1")
    launcher = tmp_path / "tmux-dispatch-prose"
    assert ensure_prose_pane(launcher, run=fake_run) == "opened"
    assert ensure_prose_pane(launcher, run=fake_run) == "already_open"
    assert calls[1][0:3] == ["tmux", "split-window", "-v"]
    assert calls[1][-1] == str(launcher)


def test_ensure_prose_pane_outside_tmux(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("TMUX", raising=False)
    assert ensure_prose_pane(tmp_path / "launcher") == "not_in_tmux"
