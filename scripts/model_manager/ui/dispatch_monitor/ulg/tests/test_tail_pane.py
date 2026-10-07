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
    BOARD_PANE_TITLE,
    PANE_TITLE,
    PROMPT_PANE_TITLE,
    configure_dispatch_status_hint,
    ensure_prompt_pane,
    ensure_prose_pane,
    format_transcript_pane_title,
    is_transcript_pane_title,
    move_selection,
    publish_board_section_focus,
    read_selection,
    tail_targets,
    update_transcript_pane_title,
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
    assert read_selection(path) == {
        "kind": "cursor-sdk",
        "key": "d1",
        "label": "d1",
        "prompt_key": "d1",
    }


def test_follow_step_retitles_on_selection_change() -> None:
    class Port:
        def tail(self, kind: str, key: str, cursor: int) -> dict:
            return {"lines": [], "cursor": 0, "eof": False}

    port = Port()
    state = FollowState()
    titles: list[str] = []

    def on_change(label: str) -> None:
        titles.append(format_transcript_pane_title(label))

    follow_step(
        state,
        {"kind": "cursor-sdk", "key": "dispatch-abc", "label": "dispatch-abc"},
        port,
        on_follow_change=on_change,
    )
    follow_step(
        state,
        {"kind": "cdp", "key": "https://x", "label": "req-other"},
        port,
        on_follow_change=on_change,
    )
    assert titles == [
        format_transcript_pane_title("dispatch-abc"),
        format_transcript_pane_title("req-other"),
    ]


def test_update_transcript_pane_title_outside_tmux(monkeypatch) -> None:
    monkeypatch.delenv("TMUX", raising=False)
    update_transcript_pane_title("any-label")


def test_update_transcript_pane_title_in_tmux(monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_run(args, **_kwargs):
        calls.append(list(args))
        if args[:2] == ["tmux", "display-message"]:
            return subprocess.CompletedProcess(args, 0, stdout="%1\n", stderr="")
        if args[:2] == ["tmux", "list-panes"]:
            return subprocess.CompletedProcess(
                args,
                0,
                stdout=(
                    f"%1\t{BOARD_PANE_TITLE}\n"
                    f"%12\t{format_transcript_pane_title('old-row')}\n"
                ),
                stderr="",
            )
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setenv("TMUX", "1")
    update_transcript_pane_title("live-dispatch-id", run=fake_run)
    expected_title = format_transcript_pane_title("live-dispatch-id")
    assert [
        "tmux",
        "select-pane",
        "-t",
        "%12",
        "-T",
        expected_title,
    ] in calls
    assert ["tmux", "select-pane", "-t", "%1"] in calls
    assert not any(
        call == ["tmux", "select-pane", "-T", expected_title] for call in calls
    )


def test_update_transcript_pane_title_skips_when_no_transcript_pane(
    monkeypatch,
) -> None:
    calls: list[list[str]] = []

    def fake_run(args, **_kwargs):
        calls.append(list(args))
        if args[:2] == ["tmux", "display-message"]:
            return subprocess.CompletedProcess(args, 0, stdout="%1\n", stderr="")
        if args[:2] == ["tmux", "list-panes"]:
            return subprocess.CompletedProcess(
                args, 0, stdout=f"%1\t{BOARD_PANE_TITLE}\n", stderr=""
            )
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setenv("TMUX", "1")
    update_transcript_pane_title("any", run=fake_run)
    assert not any(call[:3] == ["tmux", "select-pane", "-T"] for call in calls)


def test_is_transcript_pane_title() -> None:
    assert is_transcript_pane_title("transcript")
    assert is_transcript_pane_title("transcript · live-sdk-1")
    assert not is_transcript_pane_title("board")
    assert not is_transcript_pane_title("transcriptish")


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
        if args[:2] == ["tmux", "display-message"]:
            return subprocess.CompletedProcess(args, 0, stdout="%1\n", stderr="")
        if args[:2] == ["tmux", "list-panes"]:
            listed = sum(1 for call in calls if call[:2] == ["tmux", "list-panes"])
            stdout = "" if listed == 1 else f"{PANE_TITLE}\n"
            return subprocess.CompletedProcess(args, 0, stdout=stdout, stderr="")
        if args[:2] == ["tmux", "split-window"]:
            return subprocess.CompletedProcess(args, 0, stdout="%12\n", stderr="")
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setenv("TMUX", "1")
    launcher = tmp_path / "tmux-dispatch-prose"
    assert ensure_prose_pane(launcher, run=fake_run) == "opened"
    assert ensure_prose_pane(launcher, run=fake_run) == "already_open"
    splits = [call for call in calls if call[:2] == ["tmux", "split-window"]]
    assert len(splits) == 1
    assert splits[0][0:3] == ["tmux", "split-window", "-v"]
    assert splits[0][-1] == str(launcher)
    assert ["tmux", "select-pane", "-t", "%12", "-T", PANE_TITLE] in calls
    assert ["tmux", "select-pane", "-t", "%1", "-T", BOARD_PANE_TITLE] in calls
    assert ["tmux", "set-option", "-w", "pane-border-status", "top"] in calls
    assert PANE_TITLE == "transcript"


def test_ensure_prose_pane_recognizes_qualified_transcript_title(
    monkeypatch, tmp_path
) -> None:
    calls: list[list[str]] = []

    def fake_run(args, **_kwargs):
        calls.append(list(args))
        if args[:2] == ["tmux", "display-message"]:
            return subprocess.CompletedProcess(args, 0, stdout="%1\n", stderr="")
        if args[:2] == ["tmux", "list-panes"]:
            stdout = f"{format_transcript_pane_title('row-a')}\n"
            return subprocess.CompletedProcess(args, 0, stdout=stdout, stderr="")
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setenv("TMUX", "1")
    assert ensure_prose_pane(tmp_path / "launcher", run=fake_run) == "already_open"
    assert not any(call[:2] == ["tmux", "split-window"] for call in calls)


def test_ensure_prompt_pane_retitles_on_respawn(monkeypatch, tmp_path) -> None:
    calls: list[list[str]] = []

    def fake_run(args, **_kwargs):
        calls.append(list(args))
        if args[:2] == ["tmux", "display-message"]:
            return subprocess.CompletedProcess(args, 0, stdout="%1\n", stderr="")
        if args[:2] == ["tmux", "list-panes"]:
            return subprocess.CompletedProcess(
                args, 0, stdout=f"%7\t{PROMPT_PANE_TITLE}\n", stderr=""
            )
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setenv("TMUX", "1")
    assert (
        ensure_prompt_pane(tmp_path / "tmux-dispatch-prompt", run=fake_run)
        == "refreshed"
    )
    assert [
        "tmux",
        "respawn-pane",
        "-k",
        "-t",
        "%7",
        str(tmp_path / "tmux-dispatch-prompt"),
    ] in calls
    assert ["tmux", "select-pane", "-t", "%7", "-T", "prompt"] in calls
    assert ["tmux", "select-pane", "-t", "%1", "-T", BOARD_PANE_TITLE] in calls


def test_ensure_prose_pane_outside_tmux(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("TMUX", raising=False)
    assert ensure_prose_pane(tmp_path / "launcher") == "not_in_tmux"


def test_publish_board_section_focus_sets_window_option(monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_run(args, **_kwargs):
        calls.append(list(args))
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setenv("TMUX", "1")
    publish_board_section_focus("cdp", zoom=True, run=fake_run)
    assert [
        "tmux",
        "set-option",
        "-w",
        "@dispatch_board_section",
        "CDP·zoom",
    ] in calls


def test_configure_dispatch_status_hint(monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_run(args, **_kwargs):
        calls.append(list(args))
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setenv("TMUX", "1")
    configure_dispatch_status_hint(run=fake_run)
    assert ["tmux", "set-option", "-w", "status-right"] in [
        call[:4] for call in calls if call[:3] == ["tmux", "set-option", "-w"]
    ]
    assert ["tmux", "set-option", "-p", "allow-rename", "off"] in calls
    assert ["tmux", "select-pane", "-T", BOARD_PANE_TITLE] in calls
    assert BOARD_PANE_TITLE == "board"
