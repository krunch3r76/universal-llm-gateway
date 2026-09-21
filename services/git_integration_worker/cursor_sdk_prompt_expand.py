"""GIW-local prompt-expand prelude for cursor-sdk admits.

Mirrors Stargate ``expand_consume_admit_path`` gates without routing
cursor-auto nested work through ``team_dispatch`` generate.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import TYPE_CHECKING

from implement_admission.prompt_expand_admit import (
    expand_options,
    matching_root,
    should_expand,
)
from prompt_expand_consume.router import (
    ConsumeBranch,
    ConsumeDecision,
    derive_attended,
    route_consume,
    stamp_expand_provenance,
)
from universal_logging import get_logger

if TYPE_CHECKING:
    from services.git_integration_worker.models.cursor_api import CursorDispatchRequest

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class GiwExpandConsumeResult:
    """Prompt bytes plus consume routing verdict for GIW admit."""

    prompt: str
    decision: ConsumeDecision | None = None
    skip_dispatch: bool = False
    activation_envelope: dict[str, str] | None = None
    consume_advisory: bool = False


def resolve_enrolled_root_fields(thread_id: str) -> dict[str, str]:
    """Return parent/continuity root stamps when *thread_id* rolls up to a caller door."""
    from agent_bus_store.db.lane_associations import get_current_lane
    from implement_admission.prompt_expand_admit import admit_roots

    roots = admit_roots()
    tid = (thread_id or "").strip()
    if not tid:
        return {}

    enrolled = matching_root(tid, roots=roots)
    if enrolled:
        return {
            "continuity_root_thread_id": enrolled,
            "parent_dispatch_thread_id": enrolled,
        }

    try:
        lane = get_current_lane(thread_id=tid)
    except (LookupError, OSError, sqlite3.OperationalError):
        return {}

    if lane.get("state") != "associated":
        return {}

    parent = str(lane.get("parent_thread") or "").strip()
    enrolled_parent = matching_root(parent, roots=roots)
    if enrolled_parent:
        return {
            "continuity_root_thread_id": enrolled_parent,
            "parent_dispatch_thread_id": enrolled_parent,
        }
    return {}


def expand_contract_for_admit(
    req: CursorDispatchRequest,
    *,
    handoff_contract: str | None,
) -> str:
    """Contract token for prompt-expand admit — preserves operator intent on cursor-auto."""
    operator = (getattr(req, "operator_contract", None) or "").strip()
    if operator:
        return operator
    if req.handoff_contract:
        return req.handoff_contract
    return (handoff_contract or "none").strip() or "none"


def giw_should_expand_prompt(
    req: CursorDispatchRequest,
    prompt: str,
    *,
    handoff_contract: str | None,
) -> bool:
    """True when this GIW admit should run prompt-expand before ``_run_sdk_sync``."""
    contract = expand_contract_for_admit(req, handoff_contract=handoff_contract)
    decision = should_expand(
        prompt=prompt,
        contract=contract,
        caller_agent=req.caller_agent,
        parent_thread=req.parent_dispatch_thread_id,
        dispatch_thread_id=req.thread_id,
        continuity_root_thread_id=req.continuity_root_thread_id,
    )
    return decision.admit


def giw_prompt_expand_pending(
    req: CursorDispatchRequest,
    prompt: str,
    *,
    handoff_contract: str | None,
) -> str | None:
    """Return ``pending`` when the gated path will run prompt-expand."""
    if giw_should_expand_prompt(req, prompt, handoff_contract=handoff_contract):
        return "pending"
    return None


def _giw_consume_context(
    req: CursorDispatchRequest,
    prompt: str,
    *,
    attended: bool | None = None,
    durable_session: bool | None = None,
) -> tuple[bool, bool]:
    if attended is None:
        attended = derive_attended(
            transcript_id=getattr(req, "caller_transcript_id", None),
            commission_or_packet=prompt,
        )
    if durable_session is None:
        durable_session = (req.bus_lifecycle or "").strip() == "persistent"
    return attended, durable_session


def _result_from_decision(
    prompt: str,
    decision: ConsumeDecision,
    *,
    dispatch_id: str | None = None,
) -> GiwExpandConsumeResult:
    skip = decision.branch is not ConsumeBranch.SDK_BACKGROUND
    advisory = decision.branch is ConsumeBranch.CONDUCTOR_RECOMMEND
    if advisory:
        logger.info(
            "prompt-expand giw consume conductor_recommend dispatch_id=%s reason=%s",
            dispatch_id,
            decision.reason,
        )
    if decision.branch is ConsumeBranch.IN_SEAT:
        logger.info(
            "prompt-expand giw consume in_seat dispatch_id=%s reason=%s",
            dispatch_id,
            decision.reason,
        )
    return GiwExpandConsumeResult(
        prompt=prompt,
        decision=decision,
        skip_dispatch=skip,
        activation_envelope=decision.activation_header,
        consume_advisory=advisory,
    )


async def maybe_expand_giw_prompt(
    req: CursorDispatchRequest,
    prompt: str,
    *,
    handoff_contract: str | None,
    resolved_model: str,
    attended: bool | None = None,
    durable_session: bool | None = None,
) -> GiwExpandConsumeResult:
    """Run prompt-expand when enrolled; route consume; fail-open returns original."""
    original = prompt
    if not giw_should_expand_prompt(req, prompt, handoff_contract=handoff_contract):
        return GiwExpandConsumeResult(prompt=prompt)

    import asyncio

    from systems.frontier_consult.prompt_expand_prelude import run_prompt_expand

    from services.git_integration_worker.cursor_dispatch_ledger import (
        CursorDispatchLedger,
    )

    contract = expand_contract_for_admit(req, handoff_contract=handoff_contract)
    # Arming reap keys on null last_heartbeat_at (CURSOR_SDK_ARM_TIMEOUT=300s).
    # Retrieve ≤280s + cursor author ≤360s; stamp before and during the wait so
    # the parent admit stays armed work, not a pre-arm wedge (11788 hop1).
    ledger = CursorDispatchLedger.instance()

    def _heartbeat() -> None:
        ledger.bump_heartbeat(dispatch_id=req.dispatch_id)

    _heartbeat()
    result = await asyncio.to_thread(
        run_prompt_expand,
        prompt,
        expand_options(contract=contract, model=resolved_model),
        heartbeat_fn=_heartbeat,
        nest_under=req.dispatch_id,
    )
    if not result.ok:
        logger.warning(
            "prompt-expand giw fail-open dispatch_id=%s err=%s",
            req.dispatch_id,
            result.error,
        )
        return GiwExpandConsumeResult(prompt=original)

    task_prime = stamp_expand_provenance(result.prompt, result.execution_id)
    attended_flag, durable_flag = _giw_consume_context(
        req,
        original,
        attended=attended,
        durable_session=durable_session,
    )
    decision = route_consume(
        task_prime=task_prime,
        original_commission=original,
        attended=attended_flag,
        durable_session=durable_flag,
        summoning_thread_id=req.thread_id,
        transcript_id=getattr(req, "caller_transcript_id", None),
    )
    logger.info(
        "prompt-expand giw expand=%s dispatch_id=%s branch=%s",
        result.execution_id,
        req.dispatch_id,
        decision.branch.value,
    )
    from events.prompt_expand_consume import emit_expand_consume_routed

    emit_expand_consume_routed(
        execution_id=req.execution_id,
        dispatch_id=req.dispatch_id,
        door="giw",
        branch=decision.branch.value,
        reason=decision.reason,
        fire_hint=decision.fire_hint,
        operator_verb=decision.operator_verb,
        attended=attended_flag,
        durable_session=durable_flag,
        summoning_thread_id=req.parent_dispatch_thread_id or req.thread_id,
    )
    return _result_from_decision(task_prime, decision, dispatch_id=req.dispatch_id)


__all__ = [
    "GiwExpandConsumeResult",
    "expand_contract_for_admit",
    "giw_prompt_expand_pending",
    "giw_should_expand_prompt",
    "maybe_expand_giw_prompt",
    "resolve_enrolled_root_fields",
]
