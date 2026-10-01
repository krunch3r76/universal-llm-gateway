"""Dead claimed heartbeats do not defer. An open cursor-sdk closeout does."""

from __future__ import annotations

from threading import Event, Thread

import pytest

from scripts.model_manager.ui.controller.restart_drain import (
    ActiveWork,
    RestartDrainGate,
)
from services.git_integration_worker.routes.cursor_sdk import (
    retain_heartbeat_through_closeout,
    stop_closeout_heartbeat,
)


class _Probe:
    def __init__(self, work: ActiveWork) -> None:
        self._work = work

    async def snapshot(self) -> ActiveWork:
        return self._work


@pytest.mark.asyncio
async def test_sdk_closeout_in_flight_is_not_a_dead_heartbeat() -> None:
    """Restart while closeout is in flight: ticket stays busy.

    The row matches a cursor-sdk admission ticket after the run heartbeat
    would have stopped (stale age, last_heartbeat copied, no claimed_at).
    Treating that as dead finished the drain and SIGTERMed GIW mid-closeout.
    """
    closing = ActiveWork(
        busy=True,
        detail={
            "busy": True,
            "active_count": 1,
            "active_ops": [
                {
                    "op_id": "sdk-closeout",
                    "kind": "cursor_sdk",
                    "heartbeat_age_s": 180.0,
                    "last_heartbeat_at": "2026-09-30T12:00:00+00:00",
                }
            ],
        },
    )
    gate = RestartDrainGate(probes={"git_integration_worker": _Probe(closing)})
    row = (await gate.busy_report(["git_integration_worker"]))["git_integration_worker"]
    assert row["busy"] is True
    assert row["restart_would_defer"] is True
    assert row["determination"] == "busy"


@pytest.mark.asyncio
async def test_busy_status_open_sdk_ticket_without_claimed_at_stays_busy(
    tmp_path,
) -> None:
    """Production _busy_status entry for an open sdk ticket with no claimed_at.

    The probe payload is what GIW active-work carries mid-closeout. Reclassifying
    that row as dead would report idle and let the drain finish.
    """
    from unittest.mock import MagicMock

    from scripts.model_manager.ui.api_dispatch import _busy_status
    from scripts.model_manager.ui.controller.restart_intent_store import (
        RestartIntentStore,
    )

    store = RestartIntentStore(tmp_path / "restart-intents.db")
    closing = ActiveWork(
        busy=True,
        detail={
            "busy": True,
            "active_count": 1,
            "active_ops": [
                {
                    "op_id": "sdk-closeout",
                    "kind": "cursor_sdk",
                    "heartbeat_age_s": 180.0,
                    "last_heartbeat_at": "2026-09-30T12:00:00+00:00",
                }
            ],
        },
    )
    ctl = MagicMock()
    ctl.restart_intent_store = store
    ctl.restart_gate = RestartDrainGate(
        probes={"git_integration_worker": _Probe(closing)}
    )
    status = await _busy_status(ctl, service="git_integration_worker")
    assert status["busy"] is True
    assert status["restart_would_defer"] is True
    assert status["determination"] == "busy"


def test_heartbeat_stops_at_close_ticket_not_at_run_end() -> None:
    """The retained heartbeat thread is stopped by stop_closeout_heartbeat."""
    stop = Event()
    thread = Thread(target=lambda: stop.wait(5), daemon=True)
    thread.start()
    retain_heartbeat_through_closeout("sdk-closeout", thread, stop)
    assert thread.is_alive()
    stop_closeout_heartbeat("sdk-closeout")
    thread.join(timeout=1.0)
    assert not thread.is_alive()
    stop_closeout_heartbeat("sdk-closeout")


@pytest.mark.asyncio
async def test_dead_claimed_heartbeat_does_not_defer() -> None:
    """A claimed row whose heartbeat moved and then stopped is not busy."""
    dead = ActiveWork(
        busy=True,
        detail={
            "busy": True,
            "active_count": 1,
            "active_ops": [
                {
                    "op_id": "dead-claim",
                    "heartbeat_age_s": 120.0,
                    "last_heartbeat_at": "2026-09-30T12:05:00+00:00",
                    "claimed_at": "2026-09-30T12:00:00+00:00",
                }
            ],
        },
    )
    gate = RestartDrainGate(probes={"git_integration_worker": _Probe(dead)})
    row = (await gate.busy_report(["git_integration_worker"]))["git_integration_worker"]
    assert row["busy"] is False
    assert row["restart_would_defer"] is False
    assert row["determination"] == "dead_heartbeat"


@pytest.mark.asyncio
async def test_unbumped_claim_stamp_still_defers() -> None:
    stamp = "2026-09-30T12:00:00+00:00"
    work = ActiveWork(
        busy=True,
        detail={
            "busy": True,
            "active_count": 1,
            "active_ops": [
                {
                    "op_id": "concurrent-1",
                    "heartbeat_age_s": 3600.0,
                    "last_heartbeat_at": stamp,
                    "claimed_at": stamp,
                }
            ],
        },
    )
    gate = RestartDrainGate(probes={"git_integration_worker": _Probe(work)})
    row = (await gate.busy_report(["git_integration_worker"]))["git_integration_worker"]
    assert row["busy"] is True
    assert row["restart_would_defer"] is True
