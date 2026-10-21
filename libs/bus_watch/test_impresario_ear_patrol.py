"""Tests for impresario ear patrol and dispatcher wiring."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from bus_watch.impresario_ear_patrol import (
    clear_patrol_state_for_tests,
    patrol_cooldown_active,
    prescreen_house,
    run_patrol_pass,
    set_patrol_cooldown_path_for_tests,
    stamp_patrol_cooldown,
)

pytestmark = pytest.mark.offline

_ROOT = "18374"
_TRANSCRIPT = "81fb6960-950d-59c5-88b4-af3839f170d1"


def test_patrol_prescreen_skips_generating_without_model_call() -> None:
    out = prescreen_house(
        root_id=_ROOT,
        transcript_id=_TRANSCRIPT,
        glass_status="generating",
        activity_age_s=999.0,
    )
    assert out["skip_model"] is True
    assert out["reason"] == "generating"
    assert out["signals"] == []


def test_patrol_cooldown_suppresses_unchanged_fingerprint(tmp_path: Path) -> None:
    set_patrol_cooldown_path_for_tests(tmp_path / "cd.json")
    clear_patrol_state_for_tests()
    fp = f"{_ROOT}|{_TRANSCRIPT}|abc123"
    stamp_patrol_cooldown(fp, now=1000.0)
    assert patrol_cooldown_active(fp, now=1000.0 + 30.0) is True


def test_patrol_cooldown_allows_new_turn_hash(tmp_path: Path) -> None:
    set_patrol_cooldown_path_for_tests(tmp_path / "cd.json")
    clear_patrol_state_for_tests()
    fp_old = f"{_ROOT}|{_TRANSCRIPT}|hash-a"
    stamp_patrol_cooldown(fp_old, now=1000.0)
    fp_new = f"{_ROOT}|{_TRANSCRIPT}|hash-b"
    assert patrol_cooldown_active(fp_new, now=1000.0 + 30.0) is False


def test_run_dispatcher_pass_calls_patrol_beside_context_hops() -> None:
    import inspect

    from bus_watch import endpoint_wake_dispatcher as disp

    src = inspect.getsource(disp.run_dispatcher)
    assert "check_cursor_ide_context_hops" in src
    assert "run_patrol_pass" in src


def test_patrol_specimen_18374_promised_act_hop_failed_quiet_unmerged(
    tmp_path: Path,
) -> None:
    set_patrol_cooldown_path_for_tests(tmp_path / "cd.json")
    clear_patrol_state_for_tests()
    tail = (
        "Where this is going: review lane commit 7139b60eec14. "
        "Who carries it: this seat. Report again: when that review starts."
    )
    out = prescreen_house(
        root_id=_ROOT,
        transcript_id=_TRANSCRIPT,
        glass_status="completed",
        activity_age_s=120.0,
        in_flight=0,
        assistant_tail=tail,
        tool_blob="",
        tick={"now_row": "quiet", "ready": True},
        hop_failed=True,
        unmerged_child=True,
    )
    for signal in (
        "promised_act_not_fired",
        "hop_failed_stranded",
        "false_quiet_with_pending_work",
        "unharvested_or_unlanded_child",
    ):
        assert signal in out["signals"]
    assert "team_dispatch" in out["imperative"]
    assert "7139b60" in out["imperative"]
    fp = out["fingerprint"]
    stamp_patrol_cooldown(fp, now=time.time())
    again = prescreen_house(
        root_id=_ROOT,
        transcript_id=_TRANSCRIPT,
        glass_status="completed",
        activity_age_s=120.0,
        assistant_tail=tail,
        hop_failed=True,
        unmerged_child=True,
    )
    assert again.get("reason") == "cooldown"


def test_patrol_pass_enqueues_nonblocking() -> None:
    clear_patrol_state_for_tests()
    store = MagicMock()
    store.subscriptions.return_value = [
        {
            "surface": "cursor",
            "thread_scope": _ROOT,
            "agent_id": _TRANSCRIPT,
            "glass_status": "completed",
        }
    ]
    admitted: list[dict[str, Any]] = []

    def _admit(opts: dict[str, Any], **_: Any) -> None:
        admitted.append(opts)

    with patch(
        "bus_watch.impresario_ear_patrol.prescreen_house",
        return_value={
            "skip_model": True,
            "signals": ["promised_act_not_fired"],
            "fingerprint": f"{_ROOT}|{_TRANSCRIPT}|x",
            "mode": "patrol",
        },
    ):
        stats = run_patrol_pass(store, now=10_000.0, admit_pipeline=_admit)
    assert stats["enqueued"] >= 1
    assert admitted[0]["root_thread"] == _ROOT
