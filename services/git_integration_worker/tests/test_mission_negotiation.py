"""Tests for directive-loop mission negotiation (Rival B)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from services.git_integration_worker.cursor_auto import handler as handler_mod
from services.git_integration_worker.cursor_auto import queue as queue_mod
from services.git_integration_worker.cursor_auto.directive import (
    is_mission_negotiation_directive,
    parse_request_body,
)
from services.git_integration_worker.cursor_auto.mission_negotiation_handler import (
    process_mission_negotiation,
)
from services.git_integration_worker.cursor_auto.mission_negotiation_ledger import (
    MissionNegotiationLedger,
    get_negotiation_ledger,
)
from services.git_integration_worker.cursor_auto.mission_negotiation_wire import (
    CanonicalMissionPayload,
    compute_proposal_hash,
    parse_negotiation_request,
)
from services.git_integration_worker.cursor_auto.queue import AutoJob
from services.git_integration_worker.cursor_auto.supersede import (
    supersede_same_thread_inflight,
)
from services.git_integration_worker.routes.cursor_auto import EnqueueBody, enqueue


def _payload(**overrides: str) -> CanonicalMissionPayload:
    base = {
        "parent_thread": "none",
        "objective": "Ship negotiation protocol",
        "scope": "cursor_auto negotiation modules",
        "out_of_scope": "charter birth",
        "acceptance": "tests pass",
        "vision": "pre-birth mission agreement",
    }
    base.update(overrides)
    return CanonicalMissionPayload(**base)


def _negotiation_body(
    *,
    phase: str,
    negotiation_id: str | None = None,
    revision: int = 1,
    in_reply_to_turn: int = 0,
    payload: CanonicalMissionPayload | None = None,
    idle_deadline: str = "+30m",
    extra: str = "",
) -> str:
    mission = payload or _payload()
    proposal_hash = compute_proposal_hash(mission)
    nid = negotiation_id or str(uuid.uuid4())
    lines = [
        "TYPE: DIRECTIVE",
        "contract: confer",
        f"negotiation_phase: {phase}",
        f"negotiation_id: {nid}",
        f"revision: {revision}",
        f"in_reply_to_turn: {in_reply_to_turn}",
        f"proposal_hash: {proposal_hash}",
        f"parent_thread: {mission.parent_thread}",
        f"objective: {mission.objective}",
        f"scope: {mission.scope}",
        f"out_of_scope: {mission.out_of_scope}",
        f"acceptance: {mission.acceptance}",
        f"vision: {mission.vision}",
        f"idle_deadline: {idle_deadline}",
    ]
    if extra:
        lines.append(extra)
    return "\n".join(lines) + "\n"


def _job(body: str, *, turn: int = 1, from_agent: str = "web-anthropic") -> AutoJob:
    return AutoJob(
        job_id=str(uuid.uuid4()),
        thread_id="agent-bus:test-negotiation",
        turn_number=turn,
        subject="negotiation test",
        body=body,
        from_agent=from_agent,
        to_agent="cursor",
        desired_model="auto",
        desired_effort="medium",
        contract="confer",
    )


@pytest.fixture(autouse=True)
def _reset_negotiation_ledger():
    MissionNegotiationLedger.reset_for_tests()
    get_negotiation_ledger()
    yield
    MissionNegotiationLedger.reset_for_tests()


def test_parse_request_body_unchanged_without_negotiation_marker():
    """Ordinary DIRECTIVE bodies parse identically when no negotiation marker is present."""
    body = "TYPE: DIRECTIVE\ncontract: implement\nscope: todo:foo\nvision: x\n"
    parsed = parse_request_body(body)
    assert parsed is not None
    assert parsed.turn_type == "DIRECTIVE"
    assert not is_mission_negotiation_directive(body)


def test_parser_rejects_unknown_field():
    """An unrecognized body field is rejected as malformed, not silently forwarded."""
    body = _negotiation_body(phase="proposal", extra="mystery_field: nope")
    result = parse_negotiation_request(
        body, turn_type="DIRECTIVE", from_agent="web-anthropic"
    )
    assert hasattr(result, "reason")
    assert result.reason == "negotiation.malformed"


def test_parser_rejects_wrong_contract():
    """A negotiation marker paired with a non-``confer`` contract is refused."""
    body = _negotiation_body(phase="proposal").replace(
        "contract: confer", "contract: implement"
    )
    result = parse_negotiation_request(
        body, turn_type="DIRECTIVE", from_agent="web-anthropic"
    )
    assert result.reason == "negotiation.contract_refused"


def test_canonical_hash_stable():
    """The proposal hash is stable across separately constructed identical payloads."""
    mission = _payload()
    h1 = compute_proposal_hash(mission)
    h2 = compute_proposal_hash(_payload())
    assert h1 == h2
    assert h1.startswith("sha256:")


def test_ledger_revision_fencing_and_duplicate_idempotency():
    """Exact-duplicate turns are idempotent; a revision gap is refused as stale."""
    ledger = get_negotiation_ledger()
    nid = str(uuid.uuid4())
    body = _negotiation_body(phase="proposal", negotiation_id=nid)
    parsed = parse_negotiation_request(
        body, turn_type="DIRECTIVE", from_agent="web-anthropic"
    )
    first = ledger.apply_transition(
        thread_id="T1",
        negotiation_id=nid,
        phase="proposal",
        revision=1,
        proposal_hash=parsed.proposal_hash,
        payload=parsed.payload,
        in_reply_to_turn=0,
        sender="web-anthropic",
        operator_agent="web-anthropic",
        idle_deadline="+30m",
        request_turn=1,
    )
    assert first.ok is True
    dup = ledger.apply_transition(
        thread_id="T1",
        negotiation_id=nid,
        phase="proposal",
        revision=1,
        proposal_hash=parsed.proposal_hash,
        payload=parsed.payload,
        in_reply_to_turn=0,
        sender="web-anthropic",
        operator_agent="web-anthropic",
        idle_deadline="+30m",
        request_turn=1,
    )
    assert dup.duplicate is True
    stale = ledger.apply_transition(
        thread_id="T1",
        negotiation_id=nid,
        phase="counter",
        revision=3,
        proposal_hash=parsed.proposal_hash,
        payload=parsed.payload,
        in_reply_to_turn=1,
        sender="web-anthropic",
        operator_agent="web-anthropic",
        idle_deadline="+30m",
        request_turn=2,
    )
    assert stale.ok is False
    assert stale.reason == "negotiation.stale_refused"


def test_ledger_idle_expiry():
    """A negotiation past its idle deadline transitions atomically to EXPIRED."""
    ledger = get_negotiation_ledger()
    nid = str(uuid.uuid4())
    mission = _payload()
    past = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    ledger.apply_transition(
        thread_id="T-expire",
        negotiation_id=nid,
        phase="proposal",
        revision=1,
        proposal_hash=compute_proposal_hash(mission),
        payload=mission,
        in_reply_to_turn=0,
        sender="web-anthropic",
        operator_agent="web-anthropic",
        idle_deadline=past,
        request_turn=1,
    )
    expired = ledger.expire_idle("T-expire", nid)
    assert expired.ok is True
    assert expired.row is not None
    assert expired.row.state == "EXPIRED"


def test_ledger_restart_recovery():
    """A fresh ledger instance against the same DB reloads identical state."""
    ledger = get_negotiation_ledger()
    nid = str(uuid.uuid4())
    mission = _payload()
    ledger.apply_transition(
        thread_id="T-restart",
        negotiation_id=nid,
        phase="proposal",
        revision=1,
        proposal_hash=compute_proposal_hash(mission),
        payload=mission,
        in_reply_to_turn=0,
        sender="web-anthropic",
        operator_agent="web-anthropic",
        idle_deadline="+30m",
        request_turn=1,
    )
    MissionNegotiationLedger.reset_for_tests()
    reloaded = get_negotiation_ledger().get("T-restart", nid)
    assert reloaded is not None
    assert reloaded.revision == 1
    assert reloaded.state == "OPEN"


@pytest.mark.asyncio
async def test_handler_bypasses_executable_path(monkeypatch):
    q = queue_mod.reset_queue_for_tests(durable=False)
    job = _job(_negotiation_body(phase="proposal"))
    admit = AsyncMock(return_value={"blocked": None})
    supersede = AsyncMock(return_value=None)
    nested = AsyncMock(return_value={"ok": True, "dispatch_id": "auto-x"})
    cdp = AsyncMock(return_value={"ok": True})
    bus = AsyncMock()
    bus.reply = AsyncMock(return_value=type("R", (), {"status_code": 200, "body": {}})())
    with (
        patch.object(handler_mod, "blocking_admit_gate", admit),
        patch.object(handler_mod, "settle_supersede", supersede),
        patch.object(handler_mod, "submit_nested_dispatch", nested),
        patch.object(handler_mod, "commission_cdp_escalation", cdp),
    ):
        result = await process_mission_negotiation(job, bus=bus, queue=q)
    assert result["phase"] == "negotiation"
    assert result["disposition"] == "negotiation.accepted"
    admit.assert_not_called()
    nested.assert_not_called()
    cdp.assert_not_called()
    assert "TYPE: DISPOSITION" in bus.reply.await_args.kwargs["body"]
    assert "status:wait" in bus.reply.await_args.kwargs["subject"]


@pytest.mark.asyncio
async def test_process_job_negotiation_branch_before_admit(monkeypatch):
    queue_mod.reset_queue_for_tests(durable=False)
    job = _job(_negotiation_body(phase="proposal"))
    admit = AsyncMock()
    bus = AsyncMock()
    bus.reply = AsyncMock(return_value=type("R", (), {"status_code": 200, "body": {}})())
    with patch.object(handler_mod, "blocking_admit_gate", admit):
        result = await handler_mod.process_job(job, bus=bus)
    assert result["phase"] == "negotiation"
    admit.assert_not_called()


@pytest.mark.asyncio
async def test_full_exchange_proposal_agree_ratify():
    q = queue_mod.reset_queue_for_tests(durable=False)
    nid = str(uuid.uuid4())
    mission = _payload()
    bus = AsyncMock()
    bus.reply = AsyncMock(return_value=type("R", (), {"status_code": 200, "body": {}})())

    proposal = _job(_negotiation_body(phase="proposal", negotiation_id=nid), turn=1)
    r1 = await process_mission_negotiation(proposal, bus=bus, queue=q)
    assert r1["disposition"] == "negotiation.accepted"

    agree_body = _negotiation_body(
        phase="agree",
        negotiation_id=nid,
        revision=2,
        in_reply_to_turn=1,
        payload=mission,
    )
    agree = _job(agree_body, turn=2)
    r2 = await process_mission_negotiation(agree, bus=bus, queue=q)
    assert r2["disposition"] == "negotiation.agreed"

    ratify = _job(
        _negotiation_body(
            phase="ratify",
            negotiation_id=nid,
            revision=3,
            in_reply_to_turn=2,
            payload=mission,
        ),
        turn=3,
    )
    r3 = await process_mission_negotiation(ratify, bus=bus, queue=q)
    assert r3["disposition"] == "negotiation.ratified"
    row = get_negotiation_ledger().get(proposal.thread_id, nid)
    assert row is not None
    assert row.state == "RATIFIED"


@pytest.mark.asyncio
async def test_refusals_leave_state_unchanged():
    q = queue_mod.reset_queue_for_tests(durable=False)
    nid = str(uuid.uuid4())
    mission = _payload()
    bus = AsyncMock()
    bus.reply = AsyncMock(return_value=type("R", (), {"status_code": 200, "body": {}})())

    proposal = _job(_negotiation_body(phase="proposal", negotiation_id=nid), turn=1)
    await process_mission_negotiation(proposal, bus=bus, queue=q)
    before = get_negotiation_ledger().get(proposal.thread_id, nid)
    assert before is not None

    wrong_hash = _job(
        _negotiation_body(
            phase="agree",
            negotiation_id=nid,
            revision=2,
            in_reply_to_turn=1,
            payload=_payload(objective="changed"),
        ),
        turn=2,
    )
    r_hash = await process_mission_negotiation(wrong_hash, bus=bus, queue=q)
    assert r_hash["disposition"] == "negotiation.refused"
    after_hash = get_negotiation_ledger().get(proposal.thread_id, nid)
    assert after_hash.revision == before.revision
    assert after_hash.state == before.state

    stale = _job(
        _negotiation_body(
            phase="counter",
            negotiation_id=nid,
            revision=5,
            in_reply_to_turn=1,
            payload=mission,
        ),
        turn=3,
    )
    await process_mission_negotiation(stale, bus=bus, queue=q)
    after_stale = get_negotiation_ledger().get(proposal.thread_id, nid)
    assert after_stale.revision == before.revision

    wrong_sender = _job(
        _negotiation_body(
            phase="agree", negotiation_id=nid, revision=2, payload=mission
        ),
        turn=4,
        from_agent="cursor-auto",
    )
    await process_mission_negotiation(wrong_sender, bus=bus, queue=q)
    after_sender = get_negotiation_ledger().get(proposal.thread_id, nid)
    assert after_sender.revision == before.revision

    counter2 = _job(
        _negotiation_body(
            phase="counter",
            negotiation_id=nid,
            revision=2,
            in_reply_to_turn=1,
            payload=mission,
        ),
        turn=5,
    )
    await process_mission_negotiation(counter2, bus=bus, queue=q)
    counter3 = _job(
        _negotiation_body(
            phase="counter",
            negotiation_id=nid,
            revision=3,
            in_reply_to_turn=5,
            payload=mission,
        ),
        turn=6,
    )
    await process_mission_negotiation(counter3, bus=bus, queue=q)
    third = _job(
        _negotiation_body(
            phase="counter",
            negotiation_id=nid,
            revision=4,
            in_reply_to_turn=6,
            payload=mission,
        ),
        turn=7,
    )
    r_round = await process_mission_negotiation(third, bus=bus, queue=q)
    assert r_round["disposition"] == "negotiation.refused"
    final = get_negotiation_ledger().get(proposal.thread_id, nid)
    assert final.state == "ROUND_LIMIT"


@pytest.mark.asyncio
async def test_enqueue_skips_supersede_for_negotiation(monkeypatch):
    from services.git_integration_worker.admission import WorkAdmissionController
    from services.git_integration_worker.cursor_auto import liveness as liveness_mod
    from services.git_integration_worker.cursor_dispatch_ledger import (
        CursorDispatchLedger,
    )

    queue_mod.reset_queue_for_tests(durable=False)
    monkeypatch.setattr(liveness_mod.get_registry(), "is_live", lambda: True)
    controller = WorkAdmissionController(
        ledger=CursorDispatchLedger.instance(),
        worker_id="test-worker",
        pid=1234,
        worker_started_at="2026-01-01T00:00:00+00:00",
    )
    app = type("A", (), {"state": type("S", (), {"admission_controller": controller})()})()
    request = type("R", (), {"app": app})()
    supersede = AsyncMock(return_value={"method": "queue_withdraw"})
    monkeypatch.setattr(
        "services.git_integration_worker.routes.cursor_auto.supersede_same_thread_inflight",
        supersede,
    )
    body = EnqueueBody(
        thread_id="T-sup",
        turn_number=1,
        subject="neg",
        body=_negotiation_body(phase="proposal"),
        from_agent="web-anthropic",
    )
    resp = await enqueue(body, request)
    assert resp.status_code == 200
    supersede.assert_not_called()


@pytest.mark.asyncio
async def test_supersede_skips_open_negotiation():
    q = queue_mod.reset_queue_for_tests(durable=False)
    nid = str(uuid.uuid4())
    mission = _payload()
    get_negotiation_ledger().apply_transition(
        thread_id="T-open",
        negotiation_id=nid,
        phase="proposal",
        revision=1,
        proposal_hash=compute_proposal_hash(mission),
        payload=mission,
        in_reply_to_turn=0,
        sender="web-anthropic",
        operator_agent="web-anthropic",
        idle_deadline="+30m",
        request_turn=1,
    )
    q.enqueue(
        thread_id="T-open",
        turn_number=1,
        subject="old",
        body="TYPE: DIRECTIVE\ncontract: implement\nscope: todo:x\nvision: y\n",
        from_agent="web-anthropic",
        to_agent="cursor",
        desired_model="auto",
        desired_effort="medium",
        contract="implement",
    )
    new_job = _job("TYPE: DIRECTIVE\ncontract: implement\nscope: todo:z\nvision: z\n")
    new_job.thread_id = "T-open"
    result = await supersede_same_thread_inflight(new_job, queue=q)
    assert result is None
