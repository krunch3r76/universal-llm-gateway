"""Auth-gate budget — classify, count, ack, admit block, 5978 replay (friction 26462)."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from services.git_integration_worker.cursor_auto import auth_gate_budget as agb
from services.git_integration_worker.cursor_auto.auth_gate_budget import (
    classify_auth_gate,
    count_auth_gate_failures,
    effective_auth_gate_budget,
    parse_auth_gate_ack,
    pending_auth_gate_block,
    tag_gate_class_for_payload,
)
from services.git_integration_worker.cursor_auto.nested_sdk import (
    post_operator_closeout,
)
from services.git_integration_worker.cursor_auto.queue import AutoJob


def _closeout(
    *,
    turn_number: int,
    status: str,
    body_extra: str,
    meta: dict | None = None,
    dispatch_id: str = "auto-test",
) -> dict:
    meta_line = ""
    if meta is not None:
        meta_line = f"meta: {json.dumps(meta, sort_keys=True)}\n"
    body = (
        "TYPE: CLOSEOUT\n"
        f"status: {status}\n"
        f"dispatch_id: {dispatch_id}\n"
        f"{meta_line}\n"
        f"{body_extra}"
    )
    return {
        "turn_number": turn_number,
        "from": "cursor-auto",
        "subject": f"status:done — {dispatch_id}",
        "body": body,
    }


# --- Historical 5978 payloads (anti-vacuity for AC6) ---

_BODY_86E28 = """
We picked up turn 21. The real blocker is an expired NeoGov session: SIGN IN wall.

### ac_verdict

- `ac_verdict: AC1=fail reason=blocked_auth; CVS Reason For Leaving not reachable`
- `ac_verdict: AC2=fail reason=blocked_auth; Rite Aid contact field not reachable`
- `ac_verdict: AC3=fail reason=blocked_auth; Bowden phone not reachable`
- `ac_verdict: AC4=not_tested reason=blocked_auth`
- `ac_verdict: AC5=pass reason=Certify not opened; no submit`
"""

_BODY_B2DB = """
Focus password field — timed out: overlay blocks. Autofill null. SIGN IN wall.
status: blocked. password field not visible under overlay.
"""

_BODY_E03B = """
Navigating landed on SIGN IN. Status: blocked (session dead — first observation).
ac_verdict: unauthored — machine-derived envelope.
"""


def test_rule1_fires_on_complete_status_blocked_auth():
    """Founding incident auto-86e28fc8b57e — status:complete must still count."""
    turn = _closeout(
        turn_number=24,
        status="complete",
        dispatch_id="auto-86e28fc8b57e",
        meta={"closeout_source": "section2_sidecar"},
        body_extra=_BODY_86E28,
    )
    assert classify_auth_gate(turn) is True
    assert tag_gate_class_for_payload(_BODY_86E28) == "auth_gate"


def test_rule1_verdict_guard_skips_pass_session_prose():
    body = (
        "ac_verdict: AC5=pass reason=Certify not opened; session noted\n"
        "No failing auth AC lines."
    )
    turn = _closeout(turn_number=1, status="complete", body_extra=body)
    assert classify_auth_gate(turn) is False


def test_rule2_synthesized_partial_sign_in():
    turn = _closeout(
        turn_number=31,
        status="partial",
        dispatch_id="auto-b2db9311fc1b",
        meta={"closeout_source": "section2_synthesized"},
        body_extra=_BODY_B2DB,
    )
    assert classify_auth_gate(turn) is True


def test_investigate_contract_auth_counts():
    """AC3(d) rev 4 — investigate auth closeouts count; confer only excluded."""
    turn = _closeout(
        turn_number=8,
        status="partial",
        dispatch_id="auto-e03b1fe1b17a",
        meta={
            "closeout_source": "section2_synthesized",
            "contract": "investigate",
        },
        body_extra=_BODY_E03B,
    )
    assert classify_auth_gate(turn) is True


def test_confer_contract_excluded():
    turn = _closeout(
        turn_number=1,
        status="partial",
        meta={"contract": "confer", "gate_class": "auth_gate"},
        body_extra="SIGN IN blocked_auth",
    )
    assert classify_auth_gate(turn) is False


def test_negatives_do_not_count():
    # (a) AC1 field-edit blocked without auth signals
    a = _closeout(
        turn_number=1,
        status="blocked",
        body_extra="ac_verdict: AC1=fail reason=field_edit_rejected; validation",
    )
    # (b) Applying as: with auth AC passing
    b = _closeout(
        turn_number=2,
        status="complete",
        body_extra=(
            "Applying as: Kaywan Mansubi\n"
            "ac_verdict: AC0=pass reason=authenticated"
        ),
    )
    # (c) bare session dead without co-occurrence guards on Rule 1 / status
    c = _closeout(
        turn_number=3,
        status="complete",
        body_extra="infra note: session dead on unrelated MCP path",
    )
    assert classify_auth_gate(a) is False
    assert classify_auth_gate(b) is False
    assert classify_auth_gate(c) is False


def test_count_and_ack():
    op = "web-anthropic"
    turns = [
        _closeout(
            turn_number=8,
            status="partial",
            dispatch_id="auto-e03b1fe1b17a",
            body_extra=_BODY_E03B,
        ),
        _closeout(
            turn_number=24,
            status="complete",
            dispatch_id="auto-86e28fc8b57e",
            body_extra=_BODY_86E28,
        ),
    ]
    assert count_auth_gate_failures(turns, operator_from=op) == 2
    assert pending_auth_gate_block(turns, operator_from=op) is True
    turns.append(
        {
            "turn_number": 25,
            "from": op,
            "subject": "DIRECTIVE",
            "body": "auth_gate_ack: 5978\nTYPE: DIRECTIVE\n",
        }
    )
    assert count_auth_gate_failures(turns, operator_from=op) == 0
    assert pending_auth_gate_block(turns, operator_from=op) is False


def test_pre_ack_one_failure_does_not_block():
    """AC4 — pre-ack: one classified failure ⇒ not blocked."""
    op = "web-anthropic"
    turns = [
        _closeout(
            turn_number=8,
            status="partial",
            dispatch_id="auto-e03b1fe1b17a",
            body_extra=_BODY_E03B,
        ),
    ]
    assert count_auth_gate_failures(turns, operator_from=op) == 1
    assert pending_auth_gate_block(turns, operator_from=op) is False
    budget, post_ack = effective_auth_gate_budget(turns, operator_from=op)
    assert budget == 2
    assert post_ack is False


def test_post_ack_zero_failures_not_blocked_despite_dispatches():
    """AC2 — failure-counted: ack + zero auth failures ⇒ never blocked."""
    op = "web-anthropic"
    turns = [
        _closeout(
            turn_number=8,
            status="partial",
            dispatch_id="auto-e03b1fe1b17a",
            body_extra=_BODY_E03B,
        ),
        _closeout(
            turn_number=24,
            status="complete",
            dispatch_id="auto-86e28fc8b57e",
            body_extra=_BODY_86E28,
        ),
        {
            "turn_number": 25,
            "from": op,
            "subject": "DIRECTIVE",
            "body": "auth_gate_ack: 5978\nTYPE: DIRECTIVE\n",
        },
    ]
    for n in range(26, 40):
        turns.append(
            {
                "turn_number": n,
                "from": op,
                "subject": "DIRECTIVE — implement",
                "body": "TYPE: DIRECTIVE\ncontract: implement\n",
            }
        )
        turns.append(
            {
                "turn_number": n,
                "from": "cursor-auto",
                "subject": "status:done — auto-ok",
                "body": (
                    "TYPE: CLOSEOUT\nstatus: complete\n"
                    "ac_verdict: AC1=pass reason=field saved\n"
                ),
            }
        )
    assert count_auth_gate_failures(turns, operator_from=op) == 0
    assert pending_auth_gate_block(turns, operator_from=op) is False


@pytest.mark.asyncio
async def test_post_ack_one_failure_blocks_at_admit(monkeypatch):
    """AC3 — post-ack: one classified failure ⇒ blocked at admit."""
    from services.git_integration_worker.cursor_auto import admit_gates

    op = "web-anthropic"
    turns = [
        _closeout(
            turn_number=8,
            status="partial",
            dispatch_id="auto-e03b1fe1b17a",
            body_extra=_BODY_E03B,
        ),
        {
            "turn_number": 25,
            "from": op,
            "subject": "DIRECTIVE",
            "body": "auth_gate_ack: 5978\nTYPE: DIRECTIVE\n",
        },
        _closeout(
            turn_number=30,
            status="partial",
            dispatch_id="auto-b2db9311fc1b",
            body_extra=_BODY_B2DB,
        ),
    ]
    monkeypatch.setattr(
        admit_gates,
        "fetch_thread_turns",
        AsyncMock(return_value=turns),
    )
    emit = MagicMock()
    monkeypatch.setattr(
        admit_gates, "emit_frontier_sdk_auto_auth_gate_blocked", emit
    )
    terminal = AsyncMock(
        return_value={"ok": False, "terminal_status": "status:blocked"}
    )
    monkeypatch.setattr(admit_gates, "post_terminal_status", terminal)
    job = AutoJob(
        job_id="j1",
        thread_id="5978",
        turn_number=31,
        subject="DIRECTIVE — implement",
        body="TYPE: DIRECTIVE\ncontract: implement\nvision: mechanical — auth gate budget fixture\n",
        from_agent=op,
        to_agent="cursor",
        desired_model="auto",
        desired_effort="default",
        contract="implement",
    )
    result = await admit_gates.blocking_admit_gate(
        job, client=MagicMock(), queue=MagicMock()
    )
    assert result.blocked is not None
    assert result.blocked["terminal_status"] == "status:blocked"
    kwargs = terminal.await_args.kwargs
    assert kwargs["payload"]["reason"] == "auth_gate_budget_exhausted"
    assert kwargs["payload"]["post_ack"] is True
    assert kwargs["payload"]["budget"] == 1
    assert kwargs["journal_extra"]["post_ack"] is True
    assert kwargs["journal_extra"]["budget"] == 1
    emit.assert_called_once()
    assert emit.call_args.kwargs["post_ack"] is True
    assert emit.call_args.kwargs["budget"] == 1


def test_re_ack_clears_post_ack_block():
    """AC5 — second valid ack after post-ack block clears pending block."""
    op = "web-anthropic"
    turns = [
        {
            "turn_number": 25,
            "from": op,
            "subject": "DIRECTIVE",
            "body": "auth_gate_ack: 5978\nTYPE: DIRECTIVE\n",
        },
        _closeout(
            turn_number=30,
            status="partial",
            dispatch_id="auto-b2db9311fc1b",
            body_extra=_BODY_B2DB,
        ),
    ]
    assert pending_auth_gate_block(turns, operator_from=op) is True
    turns.append(
        {
            "turn_number": 32,
            "from": op,
            "subject": "DIRECTIVE",
            "body": "auth_gate_ack: auto-b2db9311fc1b\nTYPE: DIRECTIVE\n",
        }
    )
    assert count_auth_gate_failures(turns, operator_from=op) == 0
    assert pending_auth_gate_block(turns, operator_from=op) is False


@pytest.mark.asyncio
async def test_admit_gate_blocks_third_implement(monkeypatch):
    from services.git_integration_worker.cursor_auto import admit_gates

    op = "web-anthropic"
    turns = [
        _closeout(
            turn_number=8,
            status="partial",
            body_extra=_BODY_E03B,
            dispatch_id="auto-e03b1fe1b17a",
        ),
        _closeout(
            turn_number=24,
            status="complete",
            body_extra=_BODY_86E28,
            dispatch_id="auto-86e28fc8b57e",
        ),
    ]
    monkeypatch.setattr(
        admit_gates,
        "fetch_thread_turns",
        AsyncMock(return_value=turns),
    )
    emit = MagicMock()
    monkeypatch.setattr(
        admit_gates, "emit_frontier_sdk_auto_auth_gate_blocked", emit
    )
    terminal = AsyncMock(
        return_value={"ok": False, "terminal_status": "status:blocked"}
    )
    monkeypatch.setattr(admit_gates, "post_terminal_status", terminal)
    job = AutoJob(
        job_id="j1",
        thread_id="5978",
        turn_number=28,
        subject="DIRECTIVE — implement",
        body="TYPE: DIRECTIVE\ncontract: implement\nvision: mechanical — auth gate budget fixture\n",
        from_agent=op,
        to_agent="cursor",
        desired_model="auto",
        desired_effort="default",
        contract="implement",
    )
    result = await admit_gates.blocking_admit_gate(
        job, client=MagicMock(), queue=MagicMock()
    )
    assert result.blocked is not None
    assert result.blocked["terminal_status"] == "status:blocked"
    kwargs = terminal.await_args.kwargs
    assert kwargs["payload"]["reason"] == "auth_gate_budget_exhausted"
    assert kwargs["payload"]["post_ack"] is False
    assert kwargs["payload"]["budget"] == 2
    assert kwargs["journal_extra"]["gate_class"] == "auth_gate"
    assert kwargs["journal_extra"]["post_ack"] is False
    assert kwargs["journal_extra"]["budget"] == 2
    emit.assert_called_once()
    assert emit.call_args.kwargs["failure_count"] == 2
    assert emit.call_args.kwargs["post_ack"] is False


@pytest.mark.asyncio
async def test_post_operator_closeout_tags_gate_class():
    job = AutoJob(
        job_id="j1",
        thread_id="5978",
        turn_number=21,
        subject="DIRECTIVE",
        body="TYPE: DIRECTIVE",
        from_agent="web-anthropic",
        to_agent="cursor",
        desired_model="auto",
        desired_effort="default",
        contract="implement",
    )
    bus = MagicMock()
    bus.reply = AsyncMock(return_value=MagicMock(status_code=200, body="ok"))
    await post_operator_closeout(
        job,
        status="complete",
        dispatch_id="auto-86e28fc8b57e",
        model_id="cursor/composer-2.5",
        sdk_body=None,
        closeout_body=_BODY_86E28,
        closeout_source="section2_sidecar",
        bus=bus,
    )
    sent = bus.reply.await_args.kwargs["body"]
    assert "TYPE: CLOSEOUT" in sent
    assert '"gate_class": "auth_gate"' in sent
    assert '"contract": "implement"' in sent


@pytest.mark.asyncio
async def test_5978_replay_through_tagger_then_counter():
    """AC6 — historical bodies through tagger → counter; hand-written meta fails AC."""
    op = "web-anthropic"
    payloads = [
        ("auto-e03b1fe1b17a", "partial", "section2_synthesized", _BODY_E03B, 8),
        ("auto-86e28fc8b57e", "complete", "section2_sidecar", _BODY_86E28, 24),
        ("auto-b2db9311fc1b", "partial", "section2_synthesized", _BODY_B2DB, 31),
    ]
    turns: list[dict] = []
    for dispatch_id, status, source, payload, n in payloads:
        job = AutoJob(
            job_id=dispatch_id,
            thread_id="5978",
            turn_number=n - 1,
            subject="DIRECTIVE",
            body="TYPE: DIRECTIVE",
            from_agent=op,
            to_agent="cursor",
            desired_model="auto",
            desired_effort="default",
            contract=(
                "implement"
                if "86e28" in dispatch_id or "b2db" in dispatch_id
                else "investigate"
            ),
        )
        bus = MagicMock()
        bus.reply = AsyncMock(return_value=MagicMock(status_code=200, body="ok"))
        await post_operator_closeout(
            job,
            status=status,
            dispatch_id=dispatch_id,
            model_id="cursor/composer-2.5",
            sdk_body=None,
            closeout_body=payload,
            closeout_source=source,
            bus=bus,
        )
        relayed = bus.reply.await_args.kwargs["body"]
        turns.append(
            {
                "turn_number": n,
                "from": "cursor-auto",
                "subject": f"status:done — {dispatch_id}",
                "body": relayed,
            }
        )
    # At least the two implement auth failures must fire; investigate also counts.
    assert count_auth_gate_failures(turns, operator_from=op) >= 2
    assert pending_auth_gate_block(turns, operator_from=op) is True
    # Founding complete-status row must be classified.
    assert classify_auth_gate(turns[1]) is True


def test_parse_auth_gate_ack():
    assert parse_auth_gate_ack("auth_gate_ack: 5978\n") == "5978"
    assert parse_auth_gate_ack("auth_gate_ack=auto-abc\n") == "auto-abc"
    assert parse_auth_gate_ack("no ack here") is None


def test_disabled_master_switch(monkeypatch):
    monkeypatch.setattr(agb, "AUTH_GATE_BUDGET_ENABLED", False)
    turns = [
        _closeout(turn_number=1, status="partial", body_extra=_BODY_E03B),
        _closeout(turn_number=2, status="complete", body_extra=_BODY_86E28),
    ]
    assert pending_auth_gate_block(turns, operator_from="web-anthropic") is False
