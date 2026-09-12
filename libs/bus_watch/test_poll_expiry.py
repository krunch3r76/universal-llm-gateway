"""Expiry must be loud: the sliced loop emits a stall-pop line when max_hours elapses."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bus_watch.poll import sliced_wait_loop


@pytest.mark.offline
def test_expiry_emits_stall_pop_and_terminal_state(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    state_file = tmp_path / "w.state.json"

    def wait_once(_slice: int) -> dict[str, object]:  # pragma: no cover - never reached
        raise AssertionError("expired before the first poll")

    code = sliced_wait_loop(
        wait_once=wait_once,
        is_complete=lambda _snap: False,
        on_incomplete=lambda _snap: None,
        on_complete=lambda _snap: 0,
        transport_errors=(),
        on_transport_error=lambda _exc: None,
        max_hours=1e-12,
        state_file=state_file,
        heartbeat_label="unit",
        thread_id="10479",
        after_turn=51,
    )

    out = capsys.readouterr().out
    assert code == 2
    assert "watcher expired label=unit" in out
    assert "stall-pop: expired label=unit thread=10479 after_turn=51" in out
    assert json.loads(state_file.read_text())["status"] == "expired"
