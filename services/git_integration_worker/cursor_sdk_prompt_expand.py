"""GIW-local prompt-expand prelude for cursor-sdk admits.

Mirrors Stargate ``schedule_sdk_expand_and_dispatch`` gates without routing
cursor-auto nested work through ``team_dispatch`` generate.
"""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

from implement_admission.prompt_expand_admit import (
    expand_options,
    matching_root,
    should_expand,
)
from universal_logging import get_logger

if TYPE_CHECKING:
    from services.git_integration_worker.models.cursor_api import CursorDispatchRequest

logger = get_logger(__name__)


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


async def maybe_expand_giw_prompt(
    req: CursorDispatchRequest,
    prompt: str,
    *,
    handoff_contract: str | None,
    resolved_model: str,
) -> str:
    """Run prompt-expand when enrolled; fail-open returns the original *prompt*."""
    if not giw_should_expand_prompt(req, prompt, handoff_contract=handoff_contract):
        return prompt

    import asyncio

    from systems.frontier_consult.prompt_expand_prelude import run_prompt_expand

    contract = expand_contract_for_admit(req, handoff_contract=handoff_contract)
    result = await asyncio.to_thread(
        run_prompt_expand,
        prompt,
        expand_options(contract=contract, model=resolved_model),
    )
    if not result.ok:
        logger.warning(
            "prompt-expand giw fail-open dispatch_id=%s err=%s",
            req.dispatch_id,
            result.error,
        )
        return prompt
    logger.info(
        "prompt-expand giw expand=%s dispatch_id=%s",
        result.execution_id,
        req.dispatch_id,
    )
    return result.prompt


__all__ = [
    "expand_contract_for_admit",
    "giw_prompt_expand_pending",
    "giw_should_expand_prompt",
    "maybe_expand_giw_prompt",
    "resolve_enrolled_root_fields",
]
