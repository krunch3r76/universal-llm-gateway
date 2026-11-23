"""Parked houseless seat row writers and runner exits (Packet 1 AC-1, AC-2, AC-11, AC-12)."""

from __future__ import annotations

import json
import logging
import os
import sys
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from claude_bundles import cdp_registry as reg
from claude_bundles.cdp_registry.models import STATUS_TAB, _row_to_registration
from claude_bundles.cdp_registry.tab_rows import sweep_kept_tab_rows
from claude_bundles.cdp_registry.tab_rows_reopen import (
    finish_tab_on_exit,
    mark_tab_reopen_failed,
    release_tab_row,
    reopen_tab_row,
)
from claude_bundles.cse_wake_retain import registration_has_wake_debt
from claude_bundles.project_ask import ProjectAskResult

from cdp_ask.cse_session_models import CseSessionTurn, HarvestResponse
from cdp_ask.execution_resume import resume_execution
from cdp_ask.execution_store import ExecutionStore
from cdp_ask.houseless_seat_testkit import (
    CHAT_U,
    dead_pid,
    host_row,
    journal_tab_kept_count,
    seat_row,
    stamp,
)
from cdp_ask.models import SubmitProjectAskRequest
from cdp_ask.runner import run_execution

pytest_plugins = ["cdp_ask.houseless_seat_testkit"]

pytestmark = pytest.mark.offline

_RID = "R-houseless-seat"
_H = "H-pool"


def _parked_five(row: dict[str, Any]) -> None:
    assert row["status"] == STATUS_TAB
    assert row["purpose"] == "houseless"
    assert row.get("chat_url")
    assert row.get("target_id") is None
    assert row.get("tab_kept_reason") == "houseless_seat"


def _houseless_rid(active: dict[str, Any]) -> str:
    matches = [k for k, v in active.items() if v.get("purpose") == "houseless"]
    assert matches, "expected a houseless tab row in the registry"
    return matches[0]


@pytest.mark.asyncio
async def test_resumed_houseless_birth_parks_row(seat_registry: dict[str, Any]) -> None:
    reg._store.write_active(
        {
            _H: host_row(_H, 9225),
            _RID: seat_row(
                _RID,
                _H,
                9225,
                CHAT_U,
                target_id="stale",
                kept=None,
            ),
        }
    )
    stamp(_RID, "e-resume", "streaming")
    store = ExecutionStore()
    record = await store.adopt(
        execution_id="e-resume",
        registration_id=_RID,
        holder="ask-holder",
        purpose="houseless",
        parent_thread=None,
        mission_kind=None,
        started_at=1.0,
        durable_state="streaming",
    )

    async def harvest(*_a: Any, **_k: Any) -> HarvestResponse:
        return HarvestResponse(
            outcome="harvested",
            streaming=False,
            content_provenance="chat",
            turns=[CseSessionTurn(author="assistant", text="ok", source="cse-dom")],
        )

    with patch("cdp_ask.cse_session_harvest.harvest_with_loading_wait", harvest):
        await resume_execution(store, record, trigger="boot")

    active = reg._store.load_active()
    assert _RID in active, "houseless row must be present after resume"
    row = active[_RID]
    _parked_five(row)
    assert _RID not in sweep_kept_tab_rows()
    assert _RID in reg._store.load_active()
    assert journal_tab_kept_count(seat_registry["root"], _RID) == 1


@pytest.mark.asyncio
async def test_runner_houseless_birth_parks_row(seat_registry: dict[str, Any]) -> None:
    """Exception (a): ``run_project_conversation`` replaced.

    Plan AC-1 exception (a) is the second item of the rule's § Refuse; the
    acceptance proof of behaviour 1 is the resumed case, which does not patch
    the runner.
    """
    reg._store.write_active({_H: host_row(_H, 9225)})
    ok = ProjectAskResult(
        ok=True,
        body="hi",
        url=CHAT_U,
        project_uuid="",
        project_url=CHAT_U,
        model={},
        body_len=2,
        delete_after=False,
        error=None,
    )
    with (
        patch(
            "cdp_ask.runner.run_project_conversation",
            new=AsyncMock(return_value=[ok]),
        ),
        patch(
            "cdp_ask.runner.apply_project_select",
            new=AsyncMock(side_effect=lambda r, **_: r),
        ),
    ):
        req = SubmitProjectAskRequest(
            prompt_text="wake",
            purpose="houseless",
            converse=True,
            no_project_uuid=True,
            parent_thread=None,
            mission_kind=None,
        )
        payload = await run_execution(
            req,
            execution_id="e-birth",
            abort_check=AsyncMock(return_value=False),
        )
    assert payload["ok"] is True
    active = reg._store.load_active()
    rid = _houseless_rid(active)
    _parked_five(active[rid])
    assert rid not in sweep_kept_tab_rows()
    assert journal_tab_kept_count(seat_registry["root"], rid) == 1


def test_seat_branch_parks_over_wake_debt(seat_registry: dict[str, Any]) -> None:
    from claude_bundles.cse_session_obligations import mint_stop_ack_owed

    tid = "t1"
    reg._store.write_active(
        {
            _H: host_row(_H, 9225),
            _RID: seat_row(_RID, _H, 9225, CHAT_U, target_id=tid, kept=None),
        }
    )
    mint_stop_ack_owed(execution_id="e-debt", registration_id=_RID, purpose="houseless")
    assert registration_has_wake_debt(_RID)
    row = reg._store.load_active()[_RID]
    finish_tab_on_exit(_row_to_registration(row), branch="houseless_seat")
    row = reg._store.load_active()[_RID]
    assert row["tab_kept_reason"] == "houseless_seat"
    assert row.get("target_id") is None
    assert ("close", 9225, tid) in seat_registry["ops"]


@pytest.mark.parametrize(
    "action",
    [
        "release",
        "deregister",
        "normal",
        "wake_debt",
        "reopen_failed",
    ],
)
def test_parked_row_refuses_release_paths(
    seat_registry: dict[str, Any], action: str
) -> None:
    tid = "open-t"
    reg._store.write_active(
        {_H: host_row(_H, 9225), _RID: seat_row(_RID, _H, 9225, CHAT_U, target_id=tid)}
    )
    row = reg._store.load_active()[_RID]
    reg_obj = _row_to_registration(row)
    if action == "release":
        release_tab_row(_RID)
    elif action == "deregister":
        reg.deregister_lane(_RID, reason="probe_failed")
    elif action == "normal":
        finish_tab_on_exit(reg_obj, branch="normal")
    elif action == "wake_debt":
        finish_tab_on_exit(reg_obj, branch="wake_debt")
    else:
        mark_tab_reopen_failed(_RID)
    row_after = reg._store.load_active()[_RID]
    assert _RID in reg._store.load_active()
    assert row_after["tab_kept_reason"] == "houseless_seat"
    assert _RID not in sweep_kept_tab_rows()
    if action == "reopen_failed":
        assert row_after.get("target_id") is None
        assert ("close", 9225, tid) in seat_registry["ops"]


def test_parked_row_with_open_wake_tab_keeps_target(
    seat_registry: dict[str, Any],
) -> None:
    tid = "wake-open"
    reg._store.write_active(
        {_H: host_row(_H, 9225), _RID: seat_row(_RID, _H, 9225, CHAT_U, target_id=tid)}
    )
    row = reg._store.load_active()[_RID]
    reg_obj = _row_to_registration(row)
    seat_registry["ops"].clear()
    finish_tab_on_exit(reg_obj, branch="normal")
    finish_tab_on_exit(reg_obj, branch="wake_debt")
    assert reg._store.load_active()[_RID]["target_id"] == tid
    assert not [o for o in seat_registry["ops"] if o[0] == "close"]


@pytest.mark.parametrize("trigger", ["wake", "boot"])
def test_reopen_keeps_parked_reason(
    seat_registry: dict[str, Any], trigger: str
) -> None:
    reg._store.write_active(
        {
            _H: host_row(_H, 9225),
            _RID: seat_row(_RID, _H, 9225, CHAT_U, holder_pid=dead_pid()),
        }
    )
    reopen_tab_row(_RID, _H, trigger)
    row = reg._store.load_active()[_RID]
    assert row.get("target_id")
    assert row["holder_pid"] == os.getpid()
    assert row["tab_kept_reason"] == "houseless_seat"


def test_detach_parked_row_spares_host(seat_registry: dict[str, Any]) -> None:
    prof = seat_registry["profiles"] / "claude-ai-chrome-profile-p-tab"
    prof.mkdir(parents=True, exist_ok=True)
    seat_registry["live"].add(9225)
    reg._store.write_active(
        {
            _RID: seat_row(_RID, "gone-host", 9225, CHAT_U),
        }
    )
    seat_registry["kills"].clear()
    reg.detach(_RID, reason="test_detach")
    assert _RID not in reg._load_active()
    assert seat_registry["kills"] == []
    assert prof.exists()
    assert (
        not any((seat_registry["root"].parent / "trash").iterdir())
        if (seat_registry["root"].parent / "trash").exists()
        else True
    )
    last = None
    for line in (
        (seat_registry["root"] / "registry.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ):
        if line.strip():
            last = json.loads(line)
    assert last and last.get("profile_reclaim_outcome") == "skipped_tab_row"


@pytest.mark.asyncio
async def test_exit_read_failure_leaves_houseless_row(
    seat_registry: dict[str, Any],
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reg._store.write_active({_H: host_row(_H, 9225)})
    ok = ProjectAskResult(
        ok=True,
        body="hi",
        url=CHAT_U,
        project_uuid="",
        project_url=CHAT_U,
        model={},
        body_len=2,
        delete_after=False,
        error=None,
    )
    real_load = reg._load_active

    def flaky_load() -> dict[str, Any]:
        frame = sys._getframe(1)
        if frame.f_code.co_name == "run_execution":
            raise OSError("transient read")
        return real_load()

    monkeypatch.setattr(reg, "_load_active", flaky_load, raising=False)
    with (
        patch(
            "cdp_ask.runner.run_project_conversation",
            new=AsyncMock(return_value=[ok]),
        ),
        patch(
            "cdp_ask.runner.apply_project_select",
            new=AsyncMock(side_effect=lambda r, **_: r),
        ),
        caplog.at_level(logging.WARNING),
    ):
        req = SubmitProjectAskRequest(
            prompt_text="wake",
            purpose="houseless",
            converse=True,
            no_project_uuid=True,
        )
        await run_execution(
            req, execution_id="e-read-fail", abort_check=AsyncMock(return_value=False)
        )
    active = reg._store.load_active()
    rid = _houseless_rid(active)
    assert rid in active
    assert active[rid]["status"] == STATUS_TAB
    assert any(rid in rec.message for rec in caplog.records)


# --- Guards (pass at base) ---


def test_other_keep_reasons_still_swept(seat_registry: dict[str, Any]) -> None:
    rows = {}
    for i, reason in enumerate(
        ("abort_unattested", "retain_host", "teardown", "reopen_failed")
    ):
        rid = f"T{i}"
        rows[rid] = seat_row(
            rid, _H, 9225, CHAT_U, kept=reason, target_id=None, holder_pid=dead_pid()
        )
    rows[_H] = host_row(_H, 9225, status="released")
    reg._store.write_active(rows)
    got = sweep_kept_tab_rows()
    for rid in rows:
        if rid == _H:
            continue
        assert rid in got
    for rid in got:
        assert rid not in reg._store.load_active()


@pytest.mark.asyncio
async def test_not_ok_or_chatless_birth_released(seat_registry: dict[str, Any]) -> None:
    reg._store.write_active({_H: host_row(_H, 9225)})
    bad = ProjectAskResult(
        ok=False,
        body="",
        url="",
        project_uuid="",
        project_url="",
        model={},
        body_len=0,
        delete_after=False,
        error="conversation failed",
    )
    with (
        patch(
            "cdp_ask.runner.run_project_conversation", new=AsyncMock(return_value=[bad])
        ),
        patch(
            "cdp_ask.runner.apply_project_select",
            new=AsyncMock(side_effect=lambda r, **_: r),
        ),
    ):
        req = SubmitProjectAskRequest(
            prompt_text="x",
            purpose="houseless",
            converse=True,
            no_project_uuid=True,
        )
        await run_execution(
            req, execution_id="e-bad", abort_check=AsyncMock(return_value=False)
        )
    tab_rows = [
        v
        for v in reg._store.load_active().values()
        if v.get("status") == STATUS_TAB and v.get("purpose") == "houseless"
    ]
    assert not tab_rows


def test_normal_branch_still_deletes_unparked(seat_registry: dict[str, Any]) -> None:
    reg._store.write_active(
        {
            _H: host_row(_H, 9225),
            _RID: seat_row(_RID, _H, 9225, CHAT_U, kept=None, target_id="t"),
        }
    )
    finish_tab_on_exit(
        _row_to_registration(reg._store.load_active()[_RID]), branch="normal"
    )
    assert _RID not in reg._store.load_active()


def test_teardown_row_reopen_clears_reason(seat_registry: dict[str, Any]) -> None:
    reg._store.write_active(
        {
            _H: host_row(_H, 9225),
            _RID: seat_row(
                _RID, _H, 9225, CHAT_U, kept="teardown", holder_pid=dead_pid()
            ),
        }
    )
    reopen_tab_row(_RID, _H, "boot")
    assert reg._store.load_active()[_RID].get("tab_kept_reason") is None
