"""E3 followup into parked houseless seat tabs (Packet 1 AC-3, AC-4)."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import patch

import pytest
from claude_bundles import cdp_registry as reg
from claude_bundles.cdp_registry.models import RegistryError
from claude_bundles.cdp_registry.tab_rows import sweep_kept_tab_rows

from cdp_ask.execution_store import ExecutionStore
from cdp_ask.followup import execute_followup
from cdp_ask.houseless_seat_testkit import (
    CHAT_OTHER,
    CHAT_U,
    WAKE_PROMPT,
    SeatPage,
    host_row,
    paste_env,
    seat_row,
    stamp,
)
from cdp_ask.models import FollowupProjectAskRequest

pytest_plugins = ["cdp_ask.houseless_seat_testkit"]

pytestmark = pytest.mark.offline

_RID = "R-wake"
_H = "H-wake"


def _wake_req() -> FollowupProjectAskRequest:
    return FollowupProjectAskRequest(
        prompt_text=WAKE_PROMPT,
        chat_url=CHAT_U,
        registration_id=_RID,
        purpose=None,
        reattach=False,
    )


def _store_parked_host() -> None:
    reg._store.write_active(
        {
            _H: host_row(_H, 9225),
            _RID: seat_row(_RID, _H, 9225, CHAT_U, target_id=None),
            "H-other": host_row("H-other", 9224, chat_url=CHAT_OTHER),
        }
    )


@pytest.mark.asyncio
async def test_wake_pastes_into_parked_seat_and_reparks(
    seat_registry: dict[str, Any],
) -> None:
    _store_parked_host()
    before_exec = dict(reg._store.load_active()[_RID].get("execution_state") or {})

    with paste_env() as send_mock:
        store = ExecutionStore()
        resp = await execute_followup(_wake_req(), store)
    assert resp.ok is True
    assert resp.send_verified is True, getattr(resp, "receipt", None)
    assert resp.registration_id == _RID
    assert resp.reattach_used is False
    assert resp.lane_created is False
    opens = [o for o in seat_registry["ops"] if o[0] == "open"]
    closes = [o for o in seat_registry["ops"] if o[0] == "close"]
    assert len(opens) == 1 and len(closes) == 1
    send_mock.assert_awaited_once()
    assert send_mock.await_args.args[0].target_id == opens[0][2]
    row = reg._store.load_active()[_RID]
    assert row.get("target_id") is None
    assert row["tab_kept_reason"] == "houseless_seat"
    assert dict(row.get("execution_state") or {}) == before_exec


@pytest.mark.asyncio
async def test_wake_refused_for_row_mid_birth(seat_registry: dict[str, Any]) -> None:
    reg._store.write_active(
        {
            _H: host_row(_H, 9225),
            _RID: seat_row(_RID, _H, 9225, CHAT_U, target_id="mid", kept=None),
        }
    )
    stamp(_RID, "e-live", "streaming")
    with paste_env() as send_mock:
        resp = await execute_followup(_wake_req(), ExecutionStore())
    assert resp.ok is False
    assert resp.error == "houseless_seat_not_parked"
    assert not [o for o in seat_registry["ops"] if o[0] == "open"]
    send_mock.assert_not_awaited()
    row = reg._store.load_active()[_RID]
    assert row["target_id"] == "mid"


@pytest.mark.asyncio
async def test_identity_never_confirmed_reparks(
    seat_registry: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    _store_parked_host()
    monkeypatch.setenv("CDP_HOUSELESS_SEAT_IDENTITY_WAIT_S", "0.5")
    seat_registry["pages_by_tid"].clear()

    def fake_open(port: int, url: str) -> str:
        tid = "t-wrong"
        seat_registry["pages_by_tid"][tid] = SeatPage(tid, CHAT_OTHER)
        seat_registry["ops"].append(("open", port, tid, url))
        return tid

    monkeypatch.setattr("claude_bundles.cdp_lane.open_cdp_tab_target", fake_open)
    with paste_env() as send_mock:
        resp = await execute_followup(_wake_req(), ExecutionStore())
    assert resp.error == "houseless_seat_unavailable"
    assert len([o for o in seat_registry["ops"] if o[0] == "open"]) == 1
    assert len([o for o in seat_registry["ops"] if o[0] == "close"]) == 1
    send_mock.assert_not_awaited()
    row = reg._store.load_active()[_RID]
    assert row["tab_kept_reason"] == "houseless_seat"
    assert row.get("target_id") is None
    assert _RID not in sweep_kept_tab_rows()


@pytest.mark.parametrize(
    "kind,expect_ok,expect_err",
    [
        ("execution", False, "houseless_seat_in_flight"),
        ("followup", True, None),
        ("awaiting_wake", True, None),
    ],
)
@pytest.mark.asyncio
async def test_in_flight_kinds(
    seat_registry: dict[str, Any],
    kind: str,
    expect_ok: bool,
    expect_err: str | None,
) -> None:
    _store_parked_host()
    if kind == "execution":
        stamp(_RID, f"e-{kind}", "streaming", kind="execution")
    elif kind == "awaiting_wake":
        stamp(_RID, f"e-{kind}", "awaiting_wake", kind="execution")
    else:
        stamp(_RID, f"e-{kind}", "finished", kind="followup")

    with paste_env():
        resp = await execute_followup(_wake_req(), ExecutionStore())
    if expect_ok:
        assert resp.ok is True
    else:
        assert resp.error == expect_err


@pytest.mark.asyncio
async def test_second_wake_gets_lane_busy(seat_registry: dict[str, Any]) -> None:
    _store_parked_host()
    gate = asyncio.Event()
    entered = asyncio.Event()

    async def _hold_send(*_a: Any, **_k: Any) -> None:
        entered.set()
        await gate.wait()

    with paste_env() as send_mock:
        send_mock.side_effect = _hold_send
        first = asyncio.create_task(execute_followup(_wake_req(), ExecutionStore()))
        await asyncio.wait_for(entered.wait(), timeout=5)
        second = await execute_followup(_wake_req(), ExecutionStore())
        assert second.error == "lane_busy"
        assert not [o for o in seat_registry["ops"] if o[0] == "close"]
        gate.set()
        first_resp = await asyncio.wait_for(first, timeout=5)
        assert first_resp.ok is True


@pytest.mark.asyncio
async def test_no_host_unavailable_row_untouched(seat_registry: dict[str, Any]) -> None:
    before = seat_row(_RID, "gone", 9225, CHAT_U)
    reg._store.write_active({_RID: dict(before)})

    def _raise(*_a: Any, **_k: Any) -> str:
        raise RegistryError("mint refused")

    with (
        patch("claude_bundles.cdp_registry.lifecycle.register_lane", _raise),
        paste_env() as send_mock,
    ):
        resp = await execute_followup(_wake_req(), ExecutionStore())
    assert resp.error == "houseless_seat_unavailable"
    assert not [o for o in seat_registry["ops"] if o[0] == "open"]
    send_mock.assert_not_awaited()
    assert reg._store.load_active()[_RID] == before


@pytest.mark.asyncio
async def test_identity_wait_then_paste(seat_registry: dict[str, Any]) -> None:
    _store_parked_host()

    def fake_open(port: int, url: str) -> str:
        tid = "t-blank"
        seat_registry["pages_by_tid"][tid] = SeatPage(
            tid, ["about:blank", "about:blank", CHAT_U]
        )
        seat_registry["ops"].append(("open", port, tid, url))
        return tid

    with (
        patch("claude_bundles.cdp_lane.open_cdp_tab_target", fake_open),
        paste_env() as send_mock,
    ):
        resp = await execute_followup(_wake_req(), ExecutionStore())
    assert resp.ok is True
    send_mock.assert_awaited_once()
    assert reg._store.load_active()[_RID].get("tab_kept_reason") != "reopen_failed"


# --- AC-4 guards ---


@pytest.mark.asyncio
async def test_guard_abort_unattested_not_parked(seat_registry: dict[str, Any]) -> None:
    reg._store.write_active(
        {
            _H: host_row(_H, 9225),
            _RID: seat_row(
                _RID, _H, 9225, CHAT_U, kept="abort_unattested", target_id=None
            ),
        }
    )
    with paste_env() as send_mock:
        resp = await execute_followup(_wake_req(), ExecutionStore())
    assert resp.error == "houseless_seat_not_parked"
    send_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_guard_chat_mismatch(seat_registry: dict[str, Any]) -> None:
    _store_parked_host()
    req = FollowupProjectAskRequest(
        prompt_text=WAKE_PROMPT,
        chat_url=CHAT_OTHER,
        registration_id=_RID,
        reattach=False,
    )
    with paste_env() as send_mock:
        resp = await execute_followup(req, ExecutionStore())
    assert resp.error == "houseless_seat_chat_mismatch"
    send_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_guard_missing_row(seat_registry: dict[str, Any]) -> None:
    reg._store.write_active({_H: host_row(_H, 9225)})
    with paste_env() as send_mock:
        resp = await execute_followup(_wake_req(), ExecutionStore())
    assert resp.ok is False
    send_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_guard_reattach_refused(seat_registry: dict[str, Any]) -> None:
    _store_parked_host()
    req = FollowupProjectAskRequest(
        prompt_text=WAKE_PROMPT,
        chat_url=CHAT_U,
        registration_id=_RID,
        reattach=True,
    )
    with paste_env() as send_mock:
        resp = await execute_followup(req, ExecutionStore())
    assert resp.error == "houseless_no_attach"
    send_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_guard_human_visible(seat_registry: dict[str, Any]) -> None:
    _store_parked_host()
    req = FollowupProjectAskRequest(
        prompt_text=WAKE_PROMPT,
        chat_url=CHAT_U,
        registration_id=_RID,
        min_receipt="human_visible",
    )
    with paste_env() as send_mock:
        resp = await execute_followup(req, ExecutionStore())
    assert resp.error == "human_visible_receipt_unavailable"
    send_mock.assert_not_awaited()
