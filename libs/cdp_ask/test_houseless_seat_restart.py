"""Parked houseless seats across cdp_ask startup (Packet 1 AC-10)."""

from __future__ import annotations

from typing import Any

import pytest
from claude_bundles import cdp_registry as reg
from claude_bundles.cdp_registry.dormant import reap_dead_holder_tab_rows
from claude_bundles.cdp_registry.tab_rows import sweep_kept_tab_rows

from cdp_ask.execution_resume import hydrate_in_flight
from cdp_ask.execution_store import ExecutionStore
from cdp_ask.followup import execute_followup
from cdp_ask.houseless_seat_testkit import (
    CHAT_U,
    WAKE_PROMPT,
    dead_pid,
    host_row,
    paste_env,
    seat_row,
    stamp,
)
from cdp_ask.models import FollowupProjectAskRequest

pytest_plugins = ["cdp_ask.houseless_seat_testkit"]

pytestmark = pytest.mark.offline

_RID = "R-restart"
_H = "H-restart"


async def _startup_sequence() -> tuple[list[str], list[str]]:
    store = ExecutionStore()
    await hydrate_in_flight(store)
    reap = reap_dead_holder_tab_rows()
    await store.boot_reconcile()
    sweep = sweep_kept_tab_rows()
    return reap, sweep


@pytest.mark.asyncio
async def test_parked_seat_survives_restart_and_wakes(
    seat_registry: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reg._store.write_active(
        {
            _H: host_row(_H, 9225),
            _RID: seat_row(_RID, _H, 9225, CHAT_U, holder_pid=dead_pid()),
        }
    )
    stamp(_RID, "e-done", "finished")

    from claude_bundles.cdp_orphans import LivePort

    prof = seat_registry["profiles"] / f"claude-ai-chrome-profile-p-{_H}"
    monkeypatch.setattr(
        "claude_bundles.cdp_orphans.probe_live_ports",
        lambda port_range=None: [
            LivePort(port=9225, profile=prof, page_urls=(), has_live_cse=False)
        ],
    )
    reap, sweep = await _startup_sequence()
    assert _RID in reg._store.load_active()
    assert _RID not in reap
    assert _RID not in sweep
    row = reg._store.load_active()[_RID]
    assert row["tab_kept_reason"] == "houseless_seat"

    from cdp_ask.cse_session_provenance import (
        ProvenanceQuery,
        resolve_public_provenance,
    )

    prov = await resolve_public_provenance(ProvenanceQuery(registration_id=_RID))
    assert prov.purpose == "houseless"
    assert prov.status == "tab"
    assert prov.seat_open is False
    assert prov.chat_url == CHAT_U

    assert _H in reg._store.load_active(), "pool host adopted through boot_reconcile"

    wake_req = FollowupProjectAskRequest(
        prompt_text=WAKE_PROMPT,
        chat_url=CHAT_U,
        registration_id=_RID,
        purpose=None,
        reattach=False,
    )
    with paste_env():
        wake_resp = await execute_followup(wake_req, ExecutionStore())
    assert wake_resp.ok is True
    assert wake_resp.send_verified is True


@pytest.mark.asyncio
async def test_parked_seat_with_followup_stamp_keeps_reason(
    seat_registry: dict[str, Any],
) -> None:
    reg._store.write_active(
        {
            _H: host_row(_H, 9225),
            _RID: seat_row(_RID, _H, 9225, CHAT_U, holder_pid=dead_pid()),
        }
    )
    stamp(_RID, "e-fu", "streaming", kind="followup")
    reap, sweep = await _startup_sequence()
    assert _RID not in reap and _RID not in sweep
    active = reg._store.load_active()
    assert _RID in active, "parked seat must survive reap with in-flight followup"
    assert active[_RID]["tab_kept_reason"] == "houseless_seat"


def test_reap_still_removes_unparked_dead_holder(seat_registry: dict[str, Any]) -> None:
    rid = "T-dead"
    reg._store.write_active(
        {
            _H: host_row(_H, 9225),
            rid: seat_row(rid, _H, 9225, CHAT_U, kept=None, holder_pid=dead_pid()),
        }
    )
    reap = reap_dead_holder_tab_rows()
    assert rid in reap
    assert rid not in reg._store.load_active()
