"""CSE seating hook — hop occupy path + AC4 path-scoped regression."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from hop_handoff import StandingHandoffFreshness, build_continuity_handoff_body

from services.git_integration_worker.cse_session_holders import (
    ensure_schema,
    get_holder,
    upsert_holder,
)
from services.git_integration_worker.cursor_auto.continuity_hop import (
    complete_continuity_hop,
)
from services.git_integration_worker.cursor_auto.cse_seating_hook import (
    run_cse_seating_hook,
)
from services.git_integration_worker.cursor_auto.hop_cadence import (
    build_cadence_hop_body,
)
from services.git_integration_worker.cursor_auto.hop_cadence_watch import HopDecision
from services.git_integration_worker.cursor_auto.queue import AutoJob, AutoJobQueue
from services.git_integration_worker.cursor_dispatch_ledger import CursorDispatchLedger

pytestmark = pytest.mark.offline

_OCCUPY_URL = "https://claude.ai/cowork/cse_occupyhop1"
_PREDECESSOR_URL = "https://claude.ai/cowork/cse_predecessor1"
_LANE = "99001"


@pytest.fixture()
def ledger(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> CursorDispatchLedger:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    CursorDispatchLedger._instance = None
    return CursorDispatchLedger.instance()


def _hop_body(*, occupy: str | None = _OCCUPY_URL, superseded: str = "reg-old") -> str:
    return build_continuity_handoff_body(
        thread_id=_LANE,
        trigger="test-hop",
        source="agent-bus-hop-verb",
        handoff=StandingHandoffFreshness(
            status="current",
            uri=f"cortex://notes/system/threads/{_LANE}-standing-handoff.md",
            mtime_epoch=1.0,
            age_s=1.0,
        ),
        occupy_target=occupy,
        superseded_registration_id=superseded,
        successor_birth_id="c" * 32,
    )


def _job(body: str | None = None) -> AutoJob:
    return AutoJob(
        job_id="job-hop-test",
        thread_id=_LANE,
        turn_number=1,
        subject="continuity hop test",
        body=body or _hop_body(),
        from_agent="web-anthropic",
        to_agent="cursor",
        desired_model="cdp/opus-5",
        desired_effort="auto",
        contract="answer",
        continuity_hop=True,
        cse_chat_url=_OCCUPY_URL,
        cse_registration_id="reg-old",
    )


def test_handoff_emits_occupy_target_not_you_are() -> None:
    body = _hop_body()
    lines = body.splitlines()
    assert any(line.startswith("occupy_target:") for line in lines)
    assert not any(line.startswith("you_are:") for line in lines)


def test_cadence_hop_body_emits_occupy_target() -> None:
    decision = HopDecision(
        thread_id=_LANE,
        action="fire",
        reason="watch_seated_at",
        age_s=2000.0,
        threshold_s=1500.0,
        signal="watch_seated_at",
        handoff=StandingHandoffFreshness(
            status="current",
            uri=f"cortex://notes/system/threads/{_LANE}-standing-handoff.md",
            mtime_epoch=1.0,
            age_s=10.0,
        ),
    )
    body = build_cadence_hop_body(
        decision,
        registration_id="reg-old",
        chat_url=_OCCUPY_URL,
    )
    assert "occupy_target:" in body
    assert "you_are:" not in body


def test_occupy_upserts_existing_holder_and_supersedes_predecessor(
    ledger: CursorDispatchLedger,
) -> None:
    with ledger._connect() as conn:
        ensure_schema(conn)
        upsert_holder(
            conn,
            chat_url=_OCCUPY_URL,
            registration_id="reg-old",
            execution_id="exec-old",
            lane_thread_id=_LANE,
        )
        upsert_holder(
            conn,
            chat_url=_PREDECESSOR_URL,
            registration_id="reg-old",
            execution_id="exec-old",
            lane_thread_id=_LANE,
        )
        conn.commit()
    with patch(
        "services.git_integration_worker.cursor_auto.cse_seating_hook._resolve_successor_identity",
        return_value=("reg-new", _OCCUPY_URL),
    ):
        outcome = run_cse_seating_hook(_job(), execution_id="exec-new")
    assert outcome["ok"] is True
    assert outcome["path"] == "occupy_upsert"
    with ledger._connect() as conn:
        row = get_holder(conn, "cse_occupyhop1")
        pred = get_holder(conn, "cse_predecessor1")
    assert row is not None
    assert row["registration_id"] == "reg-new"
    assert row["execution_id"] == "exec-new"
    assert pred is not None
    assert pred["seat_state"] == "superseded"


def test_occupy_records_wire_registration_when_successor_has_none(
    ledger: CursorDispatchLedger,
) -> None:
    with patch(
        "services.git_integration_worker.cursor_auto.cse_seating_hook._resolve_successor_identity",
        return_value=(None, None),
    ):
        outcome = run_cse_seating_hook(_job(), execution_id="exec-new")
    assert outcome["ok"] is True
    assert outcome["registration_id"] == "reg-old"
    with ledger._connect() as conn:
        row = get_holder(conn, "cse_occupyhop1")
    assert row is not None
    assert row["registration_id"] == "reg-old"


def test_occupy_missed_mints_when_row_absent(ledger: CursorDispatchLedger) -> None:
    with patch(
        "services.git_integration_worker.cursor_auto.cse_seating_hook._resolve_successor_identity",
        return_value=("reg-new", _OCCUPY_URL),
    ):
        outcome = run_cse_seating_hook(_job(), execution_id="exec-new")
    assert outcome["ok"] is True
    assert outcome["path"] == "occupy_missed_mint"
    with ledger._connect() as conn:
        row = get_holder(conn, "cse_occupyhop1")
    assert row is not None
    assert row["lane_thread_id"] == _LANE


def test_refuse_when_occupy_target_held_by_live_peer_on_other_lane(
    ledger: CursorDispatchLedger,
) -> None:
    with ledger._connect() as conn:
        ensure_schema(conn)
        upsert_holder(
            conn,
            chat_url=_OCCUPY_URL,
            registration_id="reg-peer",
            execution_id="exec-peer",
            lane_thread_id="88888",
        )
        conn.commit()
    outcome = run_cse_seating_hook(_job(), execution_id="exec-new")
    assert outcome["ok"] is False
    assert outcome["path"] == "refused_live_peer"
    with ledger._connect() as conn:
        row = get_holder(conn, "cse_occupyhop1")
    assert row is not None
    assert row["registration_id"] == "reg-peer"
    assert row["lane_thread_id"] == "88888"


@pytest.mark.asyncio
async def test_complete_continuity_hop_runs_seating_hook_path(
    ledger: CursorDispatchLedger,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC4: hop completion must take the seating-hook path, not bypass it."""
    hook_calls: list[dict[str, object]] = []

    def _capture_hook(job: AutoJob, *, execution_id: str) -> dict[str, object]:
        hook_calls.append({"execution_id": execution_id})
        with ledger._connect() as conn:
            ensure_schema(conn)
            upsert_holder(
                conn,
                chat_url=_OCCUPY_URL,
                registration_id="reg-old",
                lane_thread_id=_LANE,
            )
            conn.commit()
        with patch(
            "services.git_integration_worker.cursor_auto.cse_seating_hook._resolve_successor_identity",
            return_value=("reg-new", _OCCUPY_URL),
        ):
            return run_cse_seating_hook(job, execution_id=execution_id)

    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.cse_seating_hook.run_cse_seating_hook",
        _capture_hook,
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.continuity_hop.commission_cdp_escalation",
        AsyncMock(return_value={"ok": True, "execution_id": "exec-new"}),
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.continuity_hop.build_hop_orientation",
        AsyncMock(
            return_value={
                "generated": False,
                "block": "",
                "inheritance_loop_closed": False,
            }
        ),
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.continuity_hop.post_harvest_residual",
        AsyncMock(return_value={"ok": True, "payload": {}}),
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.continuity_hop._post_hop_admit_report",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.continuity_hop.post_terminal_status",
        AsyncMock(return_value={"ok": True, "execution_id": "exec-new"}),
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.cse_pager_resolve.refresh_pager_after_hop",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.continuity_hop.emit_cdp_effort_bind",
        lambda **kwargs: None,
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.continuity_hop.live_run_for_thread",
        lambda _tid: None,
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.continuity_hop.split_continuity_hop_legs",
        lambda body, matched_token=None: (body, None),
    )

    q = AutoJobQueue(durable=False)
    job = _job()
    await complete_continuity_hop(job, queue=q, client=MagicMock())
    assert hook_calls, "seating hook must run on hop completion"
    assert hook_calls[0]["execution_id"] == "exec-new"


@pytest.mark.asyncio
async def test_ac4_bypass_hook_leaves_holder_stale(
    ledger: CursorDispatchLedger,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: row-correctness-at-rest alone is insufficient — path must run."""
    with ledger._connect() as conn:
        ensure_schema(conn)
        upsert_holder(
            conn,
            chat_url=_OCCUPY_URL,
            registration_id="reg-old",
            execution_id="exec-old",
            lane_thread_id=_LANE,
        )
        conn.commit()

    def _bypass_hook(job: AutoJob, *, execution_id: str) -> dict[str, object]:
        return {
            "ok": True,
            "path": "bypassed_for_test",
            "thread_id": job.thread_id,
        }

    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.cse_seating_hook.run_cse_seating_hook",
        _bypass_hook,
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.continuity_hop.commission_cdp_escalation",
        AsyncMock(return_value={"ok": True, "execution_id": "exec-new"}),
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.continuity_hop.build_hop_orientation",
        AsyncMock(
            return_value={
                "generated": False,
                "block": "",
                "inheritance_loop_closed": False,
            }
        ),
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.continuity_hop.post_harvest_residual",
        AsyncMock(return_value={"ok": True, "payload": {}}),
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.continuity_hop._post_hop_admit_report",
        AsyncMock(return_value=None),
    )

    captured_payload: dict[str, object] = {}

    async def _capture_terminal(*args, **kwargs):
        captured_payload.update(kwargs.get("payload") or {})
        return {"ok": True}

    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.continuity_hop.post_terminal_status",
        _capture_terminal,
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.cse_pager_resolve.refresh_pager_after_hop",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.continuity_hop.emit_cdp_effort_bind",
        lambda **kwargs: None,
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.continuity_hop.live_run_for_thread",
        lambda _tid: None,
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.continuity_hop.split_continuity_hop_legs",
        lambda body, matched_token=None: (body, None),
    )

    q = AutoJobQueue(durable=False)
    await complete_continuity_hop(_job(), queue=q, client=MagicMock())

    seating = captured_payload.get("seating_hook") or {}
    assert seating.get("path") == "bypassed_for_test"
    with ledger._connect() as conn:
        row = get_holder(conn, "cse_occupyhop1")
    assert row is not None
    assert row["registration_id"] == "reg-old", (
        "bypass must leave holder stale — path assertion, not row-at-rest"
    )
