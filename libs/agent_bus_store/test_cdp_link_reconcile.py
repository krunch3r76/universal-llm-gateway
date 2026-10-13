"""Tests for cdp-generate link reconcile sweep (double-timeout healing)."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import httpx
import pytest
from fastapi import FastAPI

from agent_bus_store.cdp_link_reconcile import (
    _PENDING_CLOSED,
    reconcile_cdp_dispatch_links,
)
from agent_bus_store.db import (
    admit_dispatch,
    create_thread,
    get_thread,
    get_thread_with_links,
    get_turns,
    init_db,
)
from agent_bus_store.db.connection import connect
from agent_bus_store.producer_witness import CdpLegProbeResult, WitnessVerdict
from agent_bus_store.wait_status import derive_status
from systems.frontier_consult.cdp_dispatch_envelope import (
    clear_cdp_dispatch_envelope,
    record_cdp_admit,
    reset_cdp_dispatch_envelope_for_tests,
)
from systems.frontier_consult.cdp_generate_inflight_ledger import (
    clear_inflight_ledger,
    mark_delivered,
    mark_proof_emitted,
    upsert_inflight_leg,
)
from systems.frontier_consult.cdp_leg_status_route import cdp_leg_status_router

pytestmark = pytest.mark.offline


@pytest.fixture()
def bus_db(tmp_path, monkeypatch: pytest.MonkeyPatch):
    db_path = tmp_path / "bus.db"
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(db_path))
    init_db()
    yield db_path


def _past_grace_ts() -> str:
    return (datetime.now(UTC) - timedelta(seconds=400)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _cdp_link(
    bus_db,
    *,
    slug: str,
    execution_id: str,
) -> str:
    row = create_thread(thread_id=None, slug=slug, lifecycle_state="active")
    assert row is not None
    thread_id = row["id"]
    admit_dispatch(
        thread_id=thread_id,
        execution_id=execution_id,
        pipeline_id="cdp-generate",
        caller_agent="dispatch",
    )
    with connect() as conn:
        conn.execute(
            "UPDATE thread_dispatch_links SET linked_at = ? WHERE thread_id = ?",
            (_past_grace_ts(), thread_id),
        )
    return thread_id


def _absent_probe(_execution_id: str) -> CdpLegProbeResult:
    return CdpLegProbeResult(
        WitnessVerdict.ABSENT,
        "leg_not_found",
        {"found": False},
    )


@pytest.fixture()
def stargate_leg_probe(monkeypatch: pytest.MonkeyPatch, tmp_path):
    """In-process Stargate leg route — real HTTP probe, not a mocked verdict."""
    stargate_data = tmp_path / "stargate-data"
    stargate_data.mkdir()
    monkeypatch.setenv("DATA_DIR", str(stargate_data))

    app = FastAPI()
    app.include_router(cdp_leg_status_router)
    asgi = httpx.ASGITransport(app=app)

    @contextmanager
    def _sync_client(_base_url: str, **kwargs):
        timeout = kwargs.get("timeout", 5.0)
        with httpx.Client(
            transport=asgi,
            base_url="http://stargate-leg.test",
            timeout=timeout,
        ) as client:
            yield client

    monkeypatch.setattr(
        "agent_bus_store.producer_witness.make_sync_client",
        _sync_client,
    )
    monkeypatch.setattr(
        "systems.frontier_consult.cdp_leg_status_route.terminal_status_for_execution",
        lambda _execution_id: "completed",
    )
    reset_cdp_dispatch_envelope_for_tests()
    clear_inflight_ledger()
    yield stargate_data
    reset_cdp_dispatch_envelope_for_tests()
    clear_inflight_ledger()


def _ac1e_transport_admit_closed_leg(
    *,
    execution_id: str,
    clear_envelope: bool,
) -> str:
    """Bus link committed after transport admit; Stargate leg closed and delivered."""
    row = create_thread(thread_id=None, slug="ac1e-transport", lifecycle_state="active")
    assert row is not None
    thread_id = row["id"]
    admit_dispatch(
        thread_id=thread_id,
        execution_id=execution_id,
        pipeline_id="cdp-generate",
        caller_agent="dispatch",
    )
    with connect() as conn:
        conn.execute(
            "UPDATE thread_dispatch_links SET linked_at = ? WHERE thread_id = ?",
            (_past_grace_ts(), thread_id),
        )
    record_cdp_admit(
        execution_id=execution_id,
        thread_id=thread_id,
        pointer_turn=1,
        admit_reason="transport",
        caller_supplied_thread=False,
    )
    upsert_inflight_leg(
        execution_id=execution_id,
        request_id="req-ac1e",
        thread_id=thread_id,
        pointer_turn=1,
        caller_agent="dispatch",
        prompt_uri="cortex://p.md",
        model_id="cdp/opus-5",
        max_wall_s=2400.0,
    )
    mark_proof_emitted(execution_id)
    mark_delivered(execution_id)
    if clear_envelope:
        clear_cdp_dispatch_envelope(execution_id)
    return thread_id


def test_double_pass_absent_stamps_failed_and_event(bus_db) -> None:
    thread_id = _cdp_link(bus_db, slug="absent", execution_id="exec-absent")
    with patch(
        "agent_bus_store.cdp_link_reconcile.probe_cdp_leg_status",
        side_effect=_absent_probe,
    ):
        with patch("agent_bus_store.cdp_link_reconcile.emit_dispatch_orphaned") as emit:
            assert reconcile_cdp_dispatch_links() == 0
            assert reconcile_cdp_dispatch_links() == 1
            emit.assert_called_once()
            assert emit.call_args.kwargs["reason"] == "producer_journal_absent"
    detail = get_thread_with_links(thread_id)
    assert detail is not None
    link = detail["dispatch_links"][0]
    assert link["terminal_status"] == "failed"
    turns = get_turns(thread=thread_id)
    assert sum(1 for t in turns if "cdp FAILED" in (t.get("subject") or "")) == 1
    assert reconcile_cdp_dispatch_links() == 0


def test_probe_http_404_defers_and_restarts_two_pass(bus_db) -> None:
    thread_id = _cdp_link(bus_db, slug="defer-404", execution_id="exec-defer")

    def _404(_eid: str) -> CdpLegProbeResult:
        return CdpLegProbeResult(WitnessVerdict.UNKNOWN, "probe_http_404", None)

    with patch(
        "agent_bus_store.cdp_link_reconcile.probe_cdp_leg_status",
        side_effect=_404,
    ):
        assert reconcile_cdp_dispatch_links() == 0
        with patch(
            "agent_bus_store.cdp_link_reconcile.probe_cdp_leg_status",
            side_effect=_absent_probe,
        ):
            assert reconcile_cdp_dispatch_links() == 0
            assert reconcile_cdp_dispatch_links() == 1
    detail = get_thread_with_links(thread_id)
    assert detail["dispatch_links"][0]["terminal_status"] == "failed"


def test_open_leg_live_produces_no_write(bus_db) -> None:
    thread_id = _cdp_link(bus_db, slug="live-open", execution_id="exec-live")

    def _live(_eid: str) -> CdpLegProbeResult:
        return CdpLegProbeResult(
            WitnessVerdict.LIVE,
            "leg_open",
            {"found": True, "open": True, "terminal": None, "delivered": False},
        )

    with patch(
        "agent_bus_store.cdp_link_reconcile.probe_cdp_leg_status",
        side_effect=_live,
    ):
        assert reconcile_cdp_dispatch_links() == 0
        assert reconcile_cdp_dispatch_links() == 0
    detail = get_thread_with_links(thread_id)
    assert detail["dispatch_links"][0]["terminal_status"] is None


def test_only_dead_link_stamped_on_shared_thread(bus_db) -> None:
    row = create_thread(thread_id=None, slug="dual", lifecycle_state="active")
    thread_id = row["id"]
    admit_dispatch(
        thread_id=thread_id,
        execution_id="exec-dead",
        pipeline_id="cdp-generate",
    )
    admit_dispatch(
        thread_id=thread_id,
        execution_id="exec-live",
        pipeline_id="cdp-generate",
    )
    with connect() as conn:
        conn.execute(
            "UPDATE thread_dispatch_links SET linked_at = ? WHERE thread_id = ?",
            (_past_grace_ts(), thread_id),
        )

    def _probe(eid: str) -> CdpLegProbeResult:
        if eid == "exec-dead":
            return _absent_probe(eid)
        return CdpLegProbeResult(
            WitnessVerdict.LIVE,
            "leg_open",
            {"found": True, "open": True},
        )

    with patch(
        "agent_bus_store.cdp_link_reconcile.probe_cdp_leg_status",
        side_effect=_probe,
    ):
        reconcile_cdp_dispatch_links()
        count = reconcile_cdp_dispatch_links()
    assert count == 1
    detail = get_thread_with_links(thread_id)
    by_exec = {
        link["execution_id"]: link["terminal_status"]
        for link in detail["dispatch_links"]
    }
    assert by_exec["exec-dead"] == "failed"
    assert by_exec["exec-live"] is None


def test_closed_leg_completed_two_pass_no_failed_turn(bus_db) -> None:
    thread_id = _cdp_link(bus_db, slug="closed-ok", execution_id="exec-closed-ok")

    def _closed(_eid: str) -> CdpLegProbeResult:
        return CdpLegProbeResult(
            WitnessVerdict.UNKNOWN,
            "leg_closed",
            {
                "found": True,
                "open": False,
                "terminal": "completed",
                "delivered": True,
            },
        )

    with patch(
        "agent_bus_store.cdp_link_reconcile.probe_cdp_leg_status",
        side_effect=_closed,
    ):
        with patch("agent_bus_store.cdp_link_reconcile.emit_dispatch_orphaned"):
            assert reconcile_cdp_dispatch_links() == 0
            assert reconcile_cdp_dispatch_links() == 1
    detail = get_thread_with_links(thread_id)
    assert detail["dispatch_links"][0]["terminal_status"] == "completed"
    turns = get_turns(thread=thread_id)
    assert not any("cdp FAILED" in (t.get("subject") or "") for t in turns)


def test_closed_leg_null_terminal_defers_and_emits(bus_db) -> None:
    thread_id = _cdp_link(bus_db, slug="closed-null", execution_id="exec-null-term")

    def _closed(_eid: str) -> CdpLegProbeResult:
        return CdpLegProbeResult(
            WitnessVerdict.UNKNOWN,
            "leg_closed",
            {"found": True, "open": False, "terminal": None, "delivered": False},
        )

    with patch(
        "agent_bus_store.cdp_link_reconcile.probe_cdp_leg_status",
        side_effect=_closed,
    ):
        with patch("agent_bus_store.cdp_link_reconcile.emit_dispatch_orphaned") as emit:
            assert reconcile_cdp_dispatch_links() == 0
            emit.assert_called_once()
    detail = get_thread_with_links(thread_id)
    assert detail["dispatch_links"][0]["terminal_status"] is None
    with connect() as conn:
        row = conn.execute(
            "SELECT liveness_probe_deferred_reason FROM thread_dispatch_links "
            "WHERE thread_id = ?",
            (thread_id,),
        ).fetchone()
    assert row["liveness_probe_deferred_reason"] is not None
    assert _PENDING_CLOSED not in (row["liveness_probe_deferred_reason"] or "")
