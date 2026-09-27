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


def test_occupy_leaves_registration_null_when_successor_has_none(
    ledger: CursorDispatchLedger,
) -> None:
    with patch(
        "services.git_integration_worker.cursor_auto.cse_seating_hook._resolve_successor_identity",
        return_value=(None, None),
    ):
        outcome = run_cse_seating_hook(_job(), execution_id="exec-new")
    assert outcome["ok"] is True
    assert outcome["registration_id"] is None
    assert outcome["superseded_registration_id"] == "reg-old"
    with ledger._connect() as conn:
        row = get_holder(conn, "cse_occupyhop1")
    assert row is not None
    assert row["registration_id"] is None


def test_record_seated_registration_writes_observed_id(
    ledger: CursorDispatchLedger,
) -> None:
    from services.git_integration_worker.cursor_auto.cse_seating_hook import (
        record_seated_registration,
    )

    with patch(
        "services.git_integration_worker.cursor_auto.cse_seating_hook._resolve_successor_identity",
        return_value=(None, None),
    ):
        run_cse_seating_hook(_job(), execution_id="exec-new")
    written = record_seated_registration(
        chat_url=_OCCUPY_URL,
        registration_id="reg-successor",
        execution_id="exec-seated",
    )
    assert written is not None
    assert written["registration_id"] == "reg-successor"
    assert written["execution_id"] == "exec-seated"


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


_LANE_T = "12286"
_OCCUPY_REG = "c97c246d71884a03a0ed50cb83471e4b"
_SUCCESSOR_7_URL = "https://claude.ai/cowork/cse_successor7"
_SUCCESSOR_8_URL = "https://claude.ai/cowork/cse_successor8"


def _census_row(registration_id: str) -> dict[str, str]:
    return {
        "registration_id": registration_id,
        "parent_thread": _LANE_T,
        "purpose": "operator-proxy",
        "seat_state": "active",
        "stream_state": "running",
        "execution_id": f"exec-{registration_id}",
        "source": "cse-session-registry",
    }


def test_successor_seat_retires_predecessors_and_follows_seated_row(
    ledger: CursorDispatchLedger,
) -> None:
    """AC1+AC2: second seat leaves census [successor-8]; identity is not occupy."""
    from claude_bundles.request_admission_census import census_match_ids

    from services.git_integration_worker.cursor_auto.cse_seating_hook import (
        on_successor_seated,
    )

    snap: dict[str, object] = {
        "rows": [],
        "seated_rows": [_census_row("successor-7"), _census_row(_OCCUPY_REG)],
    }
    watches = {
        _LANE_T: {
            "thread_id": _LANE_T,
            "registration_id": _OCCUPY_REG,
            "chat_url": _OCCUPY_URL,
        }
    }
    thread = {
        "cse_registration_id": _OCCUPY_REG,
        "cse_chat_url": _OCCUPY_URL,
    }
    with ledger._connect() as conn:
        ensure_schema(conn)
        upsert_holder(
            conn,
            chat_url=_OCCUPY_URL,
            registration_id=_OCCUPY_REG,
            lane_thread_id=_LANE_T,
        )
        snap = on_successor_seated(
            snap,
            parent_thread=_LANE_T,
            registration_id="successor-7",
            chat_url=_SUCCESSOR_7_URL,
            execution_id="exec-7",
            occupy_target=_OCCUPY_URL,
            conn=conn,
            watches=watches,
            thread_row=thread,
        )
        assert census_match_ids(_LANE_T, snap) == ["successor-7"]
        snap = on_successor_seated(
            snap,
            parent_thread=_LANE_T,
            registration_id="successor-8",
            chat_url=_SUCCESSOR_8_URL,
            execution_id="exec-8",
            occupy_target=_OCCUPY_URL,
            conn=conn,
            watches=watches,
            thread_row=thread,
        )
        conn.commit()
    assert census_match_ids(_LANE_T, snap) == ["successor-8"]
    with ledger._connect() as conn:
        seated = get_holder(conn, "cse_successor8")
        occupy = get_holder(conn, "cse_occupyhop1")
    assert seated is not None
    assert seated["registration_id"] == "successor-8"
    assert occupy is not None
    assert occupy["registration_id"] == _OCCUPY_REG
    assert thread["cse_registration_id"] == "successor-8"
    assert thread["cse_registration_id"] != "c97c246d"
    assert not str(thread["cse_registration_id"]).startswith("c97c246d")
    assert watches[_LANE_T]["registration_id"] == "successor-8"
    assert watches[_LANE_T]["chat_url"] == _SUCCESSOR_8_URL


def test_seated_event_writes_watch_from_payload_not_occupy() -> None:
    from services.git_integration_worker.cursor_auto.hop_cadence_stall_reconcile import (
        apply_event_to_watch,
    )

    row = {
        "thread_id": _LANE_T,
        "registration_id": _OCCUPY_REG,
        "chat_url": _OCCUPY_URL,
    }
    updated, action = apply_event_to_watch(
        row,
        {
            "signal": "cdp.generate.seated",
            "payload": {
                "parent_thread": _LANE_T,
                "registration_id": "successor-8",
                "chat_url": _SUCCESSOR_8_URL,
                "execution_id": "exec-8",
            },
        },
        now=1.0,
    )
    assert action == "seated"
    assert updated["registration_id"] == "successor-8"
    assert updated["chat_url"] == _SUCCESSOR_8_URL
    assert updated["registration_id"] != _OCCUPY_REG


def test_wire_id_outside_census_refused_and_empty_census_flags_mismatch() -> None:
    """AC3: non-empty miss refuses; empty census admits with census_mismatch."""
    from claude_bundles.request_admission_identity import gate_request_admission

    occupied = {
        "rows": [],
        "seated_rows": [_census_row("successor-8")],
    }
    with patch(
        "claude_bundles.hop_seat_cutover.load_watches",
        return_value={_LANE_T: {"thread_id": _LANE_T}},
    ):
        refused = gate_request_admission(
            thread_id=_LANE_T,
            caller_registration_id=_OCCUPY_REG,
            active_work_snap=occupied,
        )
    assert refused is not None
    assert refused["code"] == "seat.wire_id_not_in_census"
    assert refused["data"]["reason"] == "wire_id_not_in_census"

    audit: dict[str, object] = {}
    with patch(
        "claude_bundles.hop_seat_cutover.load_watches",
        return_value={_LANE_T: {"thread_id": _LANE_T}},
    ):
        admitted = gate_request_admission(
            thread_id=_LANE_T,
            caller_registration_id="attended-wire",
            active_work_snap={"rows": [], "seated_rows": []},
            audit=audit,
        )
    assert admitted is None
    assert audit["census_mismatch"] is True


def test_no_occupy_target_seats_birth_and_retires_stale_rows(
    ledger: CursorDispatchLedger,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Hop verb with no occupy_target keys the successor and drops stale ids."""
    assert ledger is CursorDispatchLedger.instance()
    watch_file = tmp_path / "hop_cadence_watches.json"
    monkeypatch.setenv("CURSOR_AUTO_HOP_WATCHES_PATH", str(watch_file))
    birth = "ab" * 16
    stale = (
        "469956ec4d2f4ce491732b205877671a",
        "1ab5aecb3a4f4889b39ed525ab3e848b",
    )
    snap = {
        "rows": [],
        "seated_rows": [_census_row(stale[0]), _census_row(stale[1])],
    }
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.cse_seating_hook._load_identity_snap",
        lambda: snap,
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.cse_seating_hook._resolve_successor_identity",
        lambda _job, _execution_id: (None, None),
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.cse_seating_hook._chat_url_from_dispatch_link",
        lambda _execution_id: None,
    )
    monkeypatch.setattr(
        "claude_bundles.cdp_registry.session_address.retire_predecessor_identity",
        lambda *_args, **_kwargs: [],
    )
    body = build_continuity_handoff_body(
        thread_id=_LANE_T,
        trigger="no-occupy",
        source="agent-bus-hop-verb",
        handoff=StandingHandoffFreshness(
            status="current",
            uri=f"cortex://notes/system/threads/{_LANE_T}-standing-handoff.md",
            mtime_epoch=1.0,
            age_s=1.0,
        ),
        occupy_target=None,
        successor_birth_id=birth,
    )
    job = AutoJob(
        job_id="job-no-occupy",
        thread_id=_LANE_T,
        turn_number=344,
        subject="continuity hop",
        body=body,
        from_agent="web-anthropic",
        to_agent="cursor",
        desired_model="cdp/fable-5.1",
        desired_effort="auto",
        contract="answer",
        continuity_hop=True,
        cse_chat_url="",
        cse_registration_id="",
    )
    execution_id = "140c033e-6e8f-4072-8ef7-ba1c06ad3d3f"
    outcome = run_cse_seating_hook(job, execution_id=execution_id)
    assert outcome["path"] == "seated_without_occupy_target"
    assert outcome["successor_seated"] is True
    assert outcome["successor_registration_id"] == birth
    assert outcome["execution_id"] == execution_id
    assert set(outcome["retired_registration_ids"]) == set(stale)

    from claude_bundles.request_admission_identity import (
        resolve_request_admission_identity,
    )

    with patch(
        "claude_bundles.request_admission_identity._resolve_origin_cse_registration",
        return_value=None,
    ):
        identity = resolve_request_admission_identity(
            thread_id=_LANE_T,
            caller_registration_id=None,
            active_work_snap=snap,
        )
    assert identity.unresolvable_reason is None
    assert identity.registration_id == birth
    assert identity.source == "watch_resume"
    assert identity.census_n == 0


def _isolate_registry(monkeypatch: pytest.MonkeyPatch, root: Path) -> None:
    """Point the CDP registry store at *root* so a seating test cannot touch live."""
    import claude_bundles.cdp_registry_store as store

    root.mkdir(parents=True, exist_ok=True)
    regs = root / "registrations"
    regs.mkdir()
    monkeypatch.setattr(store, "REGISTRY_DIR", root)
    monkeypatch.setattr(store, "REGISTRY_LOG", root / "registry.jsonl")
    monkeypatch.setattr(store, "ACTIVE_JSON", root / "active.json")
    monkeypatch.setattr(store, "PORTS_LOCK", root / "ports.lock")
    monkeypatch.setattr(store, "REGISTRATIONS_DIR", regs)
    monkeypatch.setenv("CDP_REGISTRY_SEAT_AUTHORITY", "1")


def test_no_occupy_seats_successor_holder_and_resolve(
    ledger: CursorDispatchLedger,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Birth-id hop: one driving holder, predecessor superseded, resolve follows it."""
    import json

    from claude_bundles.cdp_registry_store import load_active

    from services.git_integration_worker.cse_session_holders import (
        get_driving_holder_for_lane,
    )
    from services.git_integration_worker.cursor_auto.cse_pager_resolve import (
        resolve_live_cse_address,
    )

    lane = "99002"
    birth = "ab" * 16
    pred_reg = "517cdefbc5394176a2018b91e31e9c9f"
    foreign_reg = "ff" * 16
    seat_only_reg = "ee" * 16
    stale = "6a1176ee778f48ad9b3be616229e53e4"
    successor_url = "https://claude.ai/cowork/cse_0135zweUWn6uF4Hx7mWXGdSJ"
    pred_url = "https://claude.ai/cowork/cse_014i5jSfBoYepuvsxswuz1BP"
    scratch_url = "https://claude.ai/cowork/cse_01AoM9mfdhGSHrbPoikuwobw"
    watch_file = tmp_path / "hop_cadence_watches.json"
    monkeypatch.setenv("CURSOR_AUTO_HOP_WATCHES_PATH", str(watch_file))
    _isolate_registry(monkeypatch, tmp_path / "cdp-registry")
    # Re-read after the patch; the name imported above is the pre-patch binding.
    import claude_bundles.cdp_registry_store as store

    active = {
        pred_reg: {
            "registration_id": pred_reg,
            "parent_thread": lane,
            "seat_lane": lane,
            "seat_closed_at": None,
            "purpose": "operator-proxy",
            "chat_url": pred_url,
            "status": "active",
        },
        foreign_reg: {
            "registration_id": foreign_reg,
            "parent_thread": "88888",
            "seat_lane": "88888",
            "seat_closed_at": None,
            "purpose": "operator-proxy",
            "status": "active",
        },
        seat_only_reg: {
            "registration_id": seat_only_reg,
            "parent_thread": "",
            "seat_lane": lane,
            "seat_closed_at": None,
            "purpose": "operator-proxy",
            "status": "active",
        },
    }
    store.ACTIVE_JSON.write_text(json.dumps(active), encoding="utf-8")
    with ledger._connect() as conn:
        ensure_schema(conn)
        upsert_holder(
            conn,
            chat_url=pred_url,
            registration_id=pred_reg,
            execution_id="exec-pred",
            lane_thread_id=lane,
        )
        upsert_holder(
            conn,
            chat_url=scratch_url,
            lane_thread_id=lane,
        )
        conn.commit()
    snap = {
        "rows": [],
        "seated_rows": [
            {
                "registration_id": stale,
                "parent_thread": lane,
                "purpose": "operator-proxy",
                "seat_state": "active",
                "stream_state": "running",
                "execution_id": "exec-stale",
                "source": "cse-session-registry",
            }
        ],
    }
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.cse_seating_hook._load_identity_snap",
        lambda: snap,
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.cse_seating_hook._resolve_successor_identity",
        lambda _job, _execution_id: (None, None),
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.cse_seating_hook._chat_url_from_dispatch_link",
        lambda _execution_id: successor_url,
    )
    body = build_continuity_handoff_body(
        thread_id=lane,
        trigger="no-occupy",
        source="agent-bus-hop-verb",
        handoff=StandingHandoffFreshness(
            status="current",
            uri=f"cortex://notes/system/threads/{lane}-standing-handoff.md",
            mtime_epoch=1.0,
            age_s=1.0,
        ),
        occupy_target=None,
        successor_birth_id=birth,
    )
    job = AutoJob(
        job_id="job-seat-holder",
        thread_id=lane,
        turn_number=384,
        subject="continuity hop",
        body=body,
        from_agent="web-anthropic",
        to_agent="cursor",
        desired_model="cdp/fable-5.1",
        desired_effort="auto",
        contract="answer",
        continuity_hop=True,
        cse_chat_url="",
        cse_registration_id="",
    )
    outcome = run_cse_seating_hook(
        job, execution_id="0a0ec5eb-9380-431d-8747-40a4c732e13f"
    )
    assert outcome["path"] == "seated_without_occupy_target"
    assert outcome["holders_seated"] is True
    assert outcome["successor_registration_id"] == birth
    assert outcome["successor_chat_url"] == successor_url
    assert stale in outcome["retired_registration_ids"]
    assert pred_reg in outcome["retired_registration_ids"]

    with ledger._connect() as conn:
        driving = conn.execute(
            "SELECT registration_id, chat_url, seat_state FROM cse_session_holders "
            "WHERE lane_thread_id=? AND seat_state='driving'",
            (lane,),
        ).fetchall()
        pred = conn.execute(
            "SELECT seat_state, superseded_by FROM cse_session_holders "
            "WHERE registration_id=?",
            (pred_reg,),
        ).fetchone()
        scratch = conn.execute(
            "SELECT seat_state FROM cse_session_holders WHERE chat_url=?",
            (scratch_url,),
        ).fetchone()
        resolved_row = get_driving_holder_for_lane(conn, lane)
    assert len(driving) == 1
    assert driving[0]["registration_id"] == birth
    assert driving[0]["chat_url"] == successor_url
    assert pred is not None and pred["seat_state"] == "superseded"
    assert pred["superseded_by"] == birth
    assert scratch is not None and scratch["seat_state"] == "superseded"
    assert resolved_row is not None
    assert resolved_row["registration_id"] == birth

    resolved = resolve_live_cse_address(job)
    assert resolved["source"] == "cse_session_holders"
    assert resolved["registration_id"] == birth
    assert resolved["chat_url"] == successor_url

    after = load_active()
    assert after[pred_reg]["seat_closed_at"] is not None
    assert after[pred_reg]["seat_close_reason"] == "superseded"
    assert after[pred_reg]["superseded_by"] == birth
    assert after[foreign_reg]["seat_closed_at"] is None
    assert after[seat_only_reg]["seat_closed_at"] is not None
    assert after[seat_only_reg]["superseded_by"] == birth


def test_no_url_defers_both_stores_until_confirm(
    ledger: CursorDispatchLedger,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No window URL must not close the registry while the predecessor still drives.

    The confirm that later observes the URL seats the holder and closes the lane.
    """
    import json

    from claude_bundles.cdp_registry_store import load_active

    from services.git_integration_worker.cursor_auto.cse_seating_hook import (
        record_seated_registration,
    )

    lane = "99003"
    birth = "cd" * 16
    pred_reg = "517cdefbc5394176a2018b91e31e9c9f"
    successor_url = "https://claude.ai/cowork/cse_0135zweUWn6uF4Hx7mWXGdSJ"
    pred_url = "https://claude.ai/cowork/cse_014i5jSfBoYepuvsxswuz1BP"
    watch_file = tmp_path / "hop_cadence_watches.json"
    monkeypatch.setenv("CURSOR_AUTO_HOP_WATCHES_PATH", str(watch_file))
    _isolate_registry(monkeypatch, tmp_path / "cdp-registry-defer")
    import claude_bundles.cdp_registry_store as store

    store.ACTIVE_JSON.write_text(
        json.dumps(
            {
                pred_reg: {
                    "registration_id": pred_reg,
                    "parent_thread": lane,
                    "seat_lane": lane,
                    "seat_closed_at": None,
                    "purpose": "operator-proxy",
                    "status": "active",
                }
            }
        ),
        encoding="utf-8",
    )
    with ledger._connect() as conn:
        ensure_schema(conn)
        upsert_holder(
            conn,
            chat_url=pred_url,
            registration_id=pred_reg,
            lane_thread_id=lane,
        )
        conn.commit()
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.cse_seating_hook._load_identity_snap",
        lambda: {"rows": [], "seated_rows": []},
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.cse_seating_hook._resolve_successor_identity",
        lambda _job, _execution_id: (None, None),
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.cse_seating_hook._chat_url_from_dispatch_link",
        lambda _execution_id: None,
    )
    body = build_continuity_handoff_body(
        thread_id=lane,
        trigger="no-occupy",
        source="agent-bus-hop-verb",
        handoff=StandingHandoffFreshness(
            status="current",
            uri=f"cortex://notes/system/threads/{lane}-standing-handoff.md",
            mtime_epoch=1.0,
            age_s=1.0,
        ),
        occupy_target=None,
        successor_birth_id=birth,
    )
    job = AutoJob(
        job_id="job-defer-url",
        thread_id=lane,
        turn_number=384,
        subject="continuity hop",
        body=body,
        from_agent="web-anthropic",
        to_agent="cursor",
        desired_model="cdp/fable-5.1",
        desired_effort="auto",
        contract="answer",
        continuity_hop=True,
        cse_chat_url="",
        cse_registration_id="",
    )
    outcome = run_cse_seating_hook(
        job, execution_id="0a0ec5eb-9380-431d-8747-40a4c732e13f"
    )
    assert outcome["path"] == "seated_without_occupy_target"
    assert outcome["holders_seated"] is False
    with ledger._connect() as conn:
        driving = conn.execute(
            "SELECT registration_id FROM cse_session_holders "
            "WHERE lane_thread_id=? AND seat_state='driving'",
            (lane,),
        ).fetchall()
    assert [row["registration_id"] for row in driving] == [pred_reg]
    assert load_active()[pred_reg]["seat_closed_at"] is None

    written = record_seated_registration(
        chat_url=successor_url,
        registration_id=birth,
        execution_id="0a0ec5eb-9380-431d-8747-40a4c732e13f",
        lane_thread_id=lane,
    )
    assert written is not None
    assert written["registration_id"] == birth
    assert written["seat_state"] == "driving"
    with ledger._connect() as conn:
        driving = conn.execute(
            "SELECT registration_id, chat_url FROM cse_session_holders "
            "WHERE lane_thread_id=? AND seat_state='driving'",
            (lane,),
        ).fetchall()
        pred = conn.execute(
            "SELECT seat_state, superseded_by FROM cse_session_holders "
            "WHERE registration_id=?",
            (pred_reg,),
        ).fetchone()
    assert len(driving) == 1
    assert driving[0]["registration_id"] == birth
    assert driving[0]["chat_url"] == successor_url
    assert pred is not None and pred["seat_state"] == "superseded"
    assert pred["superseded_by"] == birth
    closed = load_active()[pred_reg]
    assert closed["seat_closed_at"] is not None
    assert closed["superseded_by"] == birth


def test_watch_retired_ids_leave_successor_as_sole_census_match() -> None:
    from claude_bundles.request_admission_identity import (
        resolve_request_admission_identity,
    )

    snap = {
        "rows": [],
        "seated_rows": [_census_row("successor-7"), _census_row("successor-8")],
    }
    with (
        patch(
            "claude_bundles.hop_seat_cutover.load_watches",
            return_value={
                _LANE_T: {
                    "thread_id": _LANE_T,
                    "retired_registration_ids": ["successor-7"],
                }
            },
        ),
        patch(
            "claude_bundles.request_admission_identity._resolve_origin_cse_registration",
            return_value=None,
        ),
    ):
        identity = resolve_request_admission_identity(
            thread_id=_LANE_T,
            caller_registration_id=None,
            active_work_snap=snap,
        )
    assert identity.census_n == 1
    assert identity.match_registration_ids == ("successor-8",)
    assert identity.registration_id == "successor-8"


def test_dispatch_link_reconcile_flips_holder_and_thread_together(
    ledger: CursorDispatchLedger,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Live-hop link seats the successor and binds the thread in one reconcile.

    A later chat_url on a different execution is not the live hop. The live
    execution with no chat_url leaves the predecessor driving.
    """
    import json

    from agent_bus_store.db import admit_dispatch, create_thread_with_turn, init_db
    from agent_bus_store.db.cse_associations import associate_cse, get_current_cse
    from agent_bus_store.db.threads_atomic import update_dispatch_link_chat_url

    from services.git_integration_worker.cursor_auto.hop_cadence_stall_reconcile import (
        reconcile_succession_confirmations,
    )

    monkeypatch.setenv("GIT_INTEGRATION_WORKER_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(tmp_path / "bus.db"))
    init_db()
    thread_row, *_ = create_thread_with_turn(
        slug="link-seat",
        from_agent="dispatch",
        to_agent="web-anthropic",
        subject="hop lane",
        body="body",
        lifecycle_state="pending",
    )
    lane = str(thread_row["id"])
    birth = "b" * 32
    pred_reg = "c9b6e8ba" * 4
    live_exec = "b7cade49-513a-4c83-b5a3-28c0a7657663"
    decoy_exec = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    successor_url = "https://claude.ai/cowork/cse_successorLink1"
    decoy_url = "https://claude.ai/cowork/cse_decoyOlderHop1"
    pred_url = "https://claude.ai/cowork/cse_predecessorDrv1"
    associate_cse(
        thread_id=lane,
        cse_chat_url=pred_url,
        cse_registration_id=pred_reg,
        bound_by="test",
        evidence="predecessor",
    )
    admit_dispatch(
        thread_id=lane,
        execution_id=live_exec,
        pipeline_id="team-dispatch",
        caller_agent="dispatch",
    )
    admit_dispatch(
        thread_id=lane,
        execution_id=decoy_exec,
        pipeline_id="team-dispatch",
        caller_agent="dispatch",
    )
    assert (
        update_dispatch_link_chat_url(
            thread_id=lane,
            execution_id=decoy_exec,
            chat_url=decoy_url,
            now_ts="2026-09-27T20:22:00Z",
        )
        == 1
    )
    with ledger._connect() as conn:
        ensure_schema(conn)
        upsert_holder(
            conn,
            chat_url=pred_url,
            registration_id=pred_reg,
            execution_id="exec-pred",
            lane_thread_id=lane,
        )
        conn.commit()
    watch_file = tmp_path / "watches.json"
    watch_file.write_text(
        json.dumps(
            {
                lane: {
                    "thread_id": lane,
                    "registration_id": pred_reg,
                    "successor_birth_id": birth,
                    "successor_execution_id": live_exec,
                    "pending_succession": {"execution_id": live_exec},
                }
            }
        ),
        encoding="utf-8",
    )
    held = reconcile_succession_confirmations(
        watches_path=watch_file,
        snapshot_reader=lambda: {"rows": []},
    )
    assert held["link_seats"][0]["reason"] == "no_dispatch_link"
    with ledger._connect() as conn:
        driving = conn.execute(
            "SELECT registration_id FROM cse_session_holders "
            "WHERE lane_thread_id=? AND seat_state='driving'",
            (lane,),
        ).fetchall()
    assert [row["registration_id"] for row in driving] == [pred_reg]
    assert get_current_cse(thread_id=lane)["cse_registration_id"] == pred_reg

    assert (
        update_dispatch_link_chat_url(
            thread_id=lane,
            execution_id=live_exec,
            chat_url=successor_url,
            now_ts="2026-09-27T20:20:07Z",
        )
        == 1
    )
    flipped = reconcile_succession_confirmations(
        watches_path=watch_file,
        snapshot_reader=lambda: {"rows": []},
    )
    seat = flipped["link_seats"][0]
    assert seat["holders_seated"] is True
    assert seat["thread_bound"] is True
    assert seat["superseded_holder_ids"]
    assert seat["successor_registration_id"] == birth
    assert seat["successor_chat_url"] == successor_url
    with ledger._connect() as conn:
        driving = conn.execute(
            "SELECT registration_id, chat_url, execution_id "
            "FROM cse_session_holders "
            "WHERE lane_thread_id=? AND seat_state='driving'",
            (lane,),
        ).fetchall()
        pred = conn.execute(
            "SELECT seat_state, superseded_by FROM cse_session_holders "
            "WHERE registration_id=?",
            (pred_reg,),
        ).fetchone()
    assert len(driving) == 1
    assert driving[0]["registration_id"] == birth
    assert driving[0]["chat_url"] == successor_url
    assert driving[0]["execution_id"] == live_exec
    assert pred is not None
    assert pred["seat_state"] == "superseded"
    assert pred["superseded_by"] == birth
    current = get_current_cse(thread_id=lane)
    assert current["cse_registration_id"] == birth
    assert current["cse_chat_url"] == successor_url
