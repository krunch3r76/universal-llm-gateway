"""Parked houseless seats across cdp_ask startup (Packet 1 AC-10)."""

from __future__ import annotations

from typing import Any

import pytest
from claude_bundles import cdp_registry as reg
from claude_bundles.cdp_registry.dormant import reap_dead_holder_tab_rows
from claude_bundles.cdp_registry.tab_rows import sweep_kept_tab_rows

from cdp_ask.execution_resume import hydrate_in_flight
from cdp_ask.execution_store import ExecutionStore
from cdp_ask.houseless_seat_testkit import (
    CHAT_U,
    dead_pid,
    host_row,
    seat_row,
    stamp,
)

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
) -> None:
    reg._store.write_active(
        {
            _H: host_row(_H, 9225),
            _RID: seat_row(_RID, _H, 9225, CHAT_U, holder_pid=dead_pid()),
        }
    )
    stamp(_RID, "e-done", "finished")
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

    # Wake delivery is covered by AC-3; restart path only proves survival + provenance.


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
    stamp(_RID, "e-fu", "finished", kind="followup")
    reap, sweep = await _startup_sequence()
    assert _RID not in reap and _RID not in sweep
    assert reg._store.load_active()[_RID]["tab_kept_reason"] == "houseless_seat"


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
