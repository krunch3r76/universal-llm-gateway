"""Live-tick falsifiers for window_terminal_contract — post charter_reload (AC7).

Uses live agent-bus + cortex substrates; run only after deploy + charter_reload.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from scripts.model_manager.ui.controller.charter_runner.admission import CapStore
from scripts.model_manager.ui.controller.charter_runner.checkpoint_admit_gate import (
    validate_arc_for_admit,
    validate_checkpoint_for_admit,
)
from scripts.model_manager.ui.controller.charter_runner.checkpoint_schema import (
    parse_checkpoint,
)
from scripts.model_manager.ui.controller.charter_runner.admission import (
    evaluate_root,
)
from scripts.model_manager.ui.controller.charter_runner.executor_routing import (
    resolve_charter_executor,
)
from scripts.model_manager.ui.controller.charter_runner.giw_live_hold import (
    build_tick_env_snapshot,
)
from scripts.model_manager.ui.controller.charter_runner.live_falsifier_harness import (
    live_assertion_get,
    live_density_triage_lookup,
)
from scripts.model_manager.ui.controller.charter_runner import kernel as tick_loop

_ROOT = "5975"
_FRESH_ROOT = "5975-live-falsifier"
_JUDGMENT_TODO = "todo:cursor-auto-in-seat-nested-terminal"
_ARC_REFUSAL_HINT = (
    "G-row requires r_admit_required arc (density_triage=judgment_required) — "
    "admit r_admit_required consult/recon seat, not mechanical in-seat lane."
)

_MECHANICAL_JUDGMENT_BODY = f"""\
TYPE: CHECKPOINT

## Anchor
- Todo: {_JUDGMENT_TODO} · workflow_state=implement_ready

## In-flight / WIP
none

## Next pickup
1. G4 — land the bind · executor_lane: implement · {_JUDGMENT_TODO}

## Steps
1. [ ] G4 — implement

## Frictions
_None this window._

## Sidecars
_None this window._

— RESUME (any seat, no command): load agent-bus-discipline → scoreboard → CHECKPOINT.
"""


def _turn(n: int, subject: str, body: str = "") -> dict[str, Any]:
    return {"turn_number": n, "subject": subject, "body": body}


async def _fetch_live_turns(root_id: str = _ROOT) -> list[dict[str, Any]]:
    from scripts.model_manager.ui.controller.charter_runner import bus_client

    return await bus_client.fetch_turns(root_id)


def _turns_through(turns: list[dict[str, Any]], max_turn: int) -> list[dict[str, Any]]:
    filtered = [t for t in turns if int(t.get("turn_number") or 0) <= max_turn]
    return sorted(filtered, key=lambda t: int(t.get("turn_number") or 0))


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


@pytest.mark.integration
def test_falsifier_checkpoint_subject_consult_body_admits_consult_within_one_cycle() -> None:
    """CHECKPOINT subject + CONSULT_PENDING in body ⇒ consult admit ≤1 eligibility cycle."""
    live = _run(_fetch_live_turns())
    # Wave 7 terminal: CHECKPOINT subject, CONSULT_PENDING in body (turn 23).
    turns = _turns_through(live, 23)
    assert any(
        str(t.get("subject") or "").upper().startswith("CHECKPOINT")
        and "CONSULT_PENDING" in str(t.get("body") or "")
        for t in turns
    )
    env = _run(build_tick_env_snapshot())
    decision = evaluate_root(_FRESH_ROOT, turns, CapStore(), env_snapshot=env)
    assert decision.eligible is True, decision.reason
    assert decision.reason == "eligible_consult"
    assert decision.window_kind == "consult"


@pytest.mark.integration
def test_falsifier_consult_pending_subject_admits_consult_within_one_cycle() -> None:
    """CONSULT_PENDING subject (5975 turn-7 shape) ⇒ consult admit ≤1 cycle."""
    live = _run(_fetch_live_turns())
    turns = _turns_through(live, 7)
    terminal = next(
        t
        for t in reversed(turns)
        if str(t.get("subject") or "").startswith("CONSULT_PENDING")
    )
    assert str(terminal.get("subject") or "").startswith("CONSULT_PENDING")
    env = _run(build_tick_env_snapshot())
    decision = evaluate_root(_FRESH_ROOT, turns, CapStore(), env_snapshot=env)
    assert decision.eligible is True, decision.reason
    assert decision.reason == "eligible_consult"
    assert decision.window_kind == "consult"
    assert decision.parsed is not None
    assert decision.parsed.consult_role == "r_admit"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_falsifier_protocol_friction_mints_single_followon_via_harvest_hook() -> None:
    """Actionable protocol friction ⇒ exactly one todo:friction-* via harvest hook."""
    live = await _fetch_live_turns()
    # Window 8 closeout (turn 26) cites one filed protocol friction (a:26459).
    checkpoint = next(
        t for t in live if int(t.get("turn_number") or 0) == 26
    )
    body = str(checkpoint.get("body") or "")
    assert "assertion:26459" in body

    mint_calls: list[str] = []

    def _track_mint(assertion: dict[str, Any], *, root_id: str) -> str:
        mint_calls.append(str(assertion.get("id") or ""))
        return f"todo:friction-{assertion.get('id')}"

    with (
        patch(
            "cortex_store.dispatch_ops._friction_enqueue.mint_friction_followon",
            side_effect=_track_mint,
        ),
        patch(
            "cortex_store.dispatch_ops.ops_assertions_update._op_assertion_get",
            side_effect=lambda *, assertion_id: live_assertion_get(assertion_id),
        ),
        patch(
            "cortex_store.dispatch_ops.ops_assertions._op_frictions",
            return_value={"items": [{"id": 26459}]},
        ),
    ):
        from scripts.model_manager.ui.controller.charter_runner.window_terminal_contract import (
            after_window_terminal_harvested,
        )

        await after_window_terminal_harvested(
            root_id=_ROOT,
            window_index=8,
            checkpoint_turn=26,
            checkpoint_subject=str(checkpoint.get("subject") or ""),
            checkpoint_body=body,
            worker_turns=[],
            worker_closed=True,
            gate_bypass_count=0,
        )

    assert len(mint_calls) == 1, mint_calls


@pytest.mark.integration
def test_falsifier_judgment_required_row_refused_mechanical_lane(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """judgment_required todo admitted mechanical ⇒ refused with arc hint."""
    triage = live_density_triage_lookup(_JUDGMENT_TODO)
    assert triage == "judgment_required"

    parsed = parse_checkpoint(_MECHANICAL_JUDGMENT_BODY)
    bind = resolve_charter_executor(
        parsed=parsed,
        admission_mode="autonomous",
        consult_role=None,
    )
    assert bind.lane == "implement"

    verdict = validate_arc_for_admit(
        parsed,
        window_kind="worker",
        admission_mode="autonomous",
        consult_role=None,
        executor_lane="implement",
        density_triage_lookup=live_density_triage_lookup,
    )
    assert verdict is not None
    assert verdict.ok is False
    assert verdict.reason == "arc_lane_too_weak"
    assert verdict.fix_hint == _ARC_REFUSAL_HINT

    schema = validate_checkpoint_for_admit(_MECHANICAL_JUDGMENT_BODY)
    assert schema.ok is True

    turns = [_turn(1, "CHECKPOINT wave — G3 mechanical attempt", _MECHANICAL_JUDGMENT_BODY)]
    env = _run(build_tick_env_snapshot())
    with patch(
        "scripts.model_manager.ui.controller.charter_runner.window_terminal_contract.default_density_triage_lookup",
        live_density_triage_lookup,
    ):
        decision = evaluate_root(_FRESH_ROOT, turns, CapStore(), env_snapshot=env)
    assert decision.eligible is False, decision.reason
    assert decision.reason == "arc_lane_too_weak"
