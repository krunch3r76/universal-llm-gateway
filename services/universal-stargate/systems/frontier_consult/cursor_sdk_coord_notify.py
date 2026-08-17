"""Coordination-thread admit pointers when worker ≠ parent dispatch thread."""

from __future__ import annotations

import os
from typing import Any

import httpx
from transport_utils import DEFAULT_AGENT_BUS_URL, make_async_client
from universal_logging import get_logger

from .cursor_sdk_admit_loop import (
    classify_admit_pointer_loop,
    increment_admit_pointer_would_have_refused,
)
from .cursor_sdk_generate_signals import publish_frontier_event
from .events import FrontierAdmitPointerLoopClosure

logger = get_logger(__name__)

_ADMIT_SUBJECT = "cursor-sdk generate admitted"


def _bus_headers() -> dict[str, str]:
    token = os.getenv("AGENT_BUS_TOKEN", "").strip()
    return {"Authorization": f"Bearer {token}"} if token else {}


def _bus_token_configured() -> bool:
    if os.getenv("AGENT_BUS_TOKEN", "").strip():
        return True
    return os.getenv("ALLOW_UNSET_AGENT_BUS_TOKEN", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )


def _admit_body(*, worker_thread_id: str, contract: str) -> str:
    lines = [
        f"Worker thread `{worker_thread_id}` — poll via `poll_hint` from the 202 "
        "response (not this coordination thread).",
    ]
    if contract == "implement":
        lines.append(
            "contract=implement: deliverable also stages on the bound todo — "
            "entity_get after worker closeout."
        )
    return "\n".join(lines)


def _emit_loop_closure_signal(
    *,
    request_id: str | None,
    execution_id: str | None,
    admit_target_thread: str,
    prompt_source_thread: str,
    prompt_bind_mode: str | None,
    prompt_turn_number: int | None,
    has_explicit_prompt_source: bool,
    refused: bool = False,
) -> bool:
    """Emit loop_closure when classified. Returns True when loop_closure."""
    classification = classify_admit_pointer_loop(
        admit_target_thread=admit_target_thread,
        prompt_source_thread=prompt_source_thread,
        prompt_bind_mode=prompt_bind_mode,
        prompt_turn_number=prompt_turn_number,
        has_explicit_prompt_source=has_explicit_prompt_source,
    )
    if not classification.loop_closure:
        return False
    total = 0
    if classification.would_have_refused:
        total = increment_admit_pointer_would_have_refused()
    logger.warning(
        "admit_pointer loop_closure (B.3 refuse=%s): "
        "admit_target=%s prompt_source=%s mode=%s reason=%s "
        "would_have_refused=%s total=%s",
        refused,
        admit_target_thread,
        prompt_source_thread,
        prompt_bind_mode,
        classification.reason,
        classification.would_have_refused,
        total,
    )
    publish_frontier_event(
        FrontierAdmitPointerLoopClosure(
            request_id=request_id,
            execution_id=execution_id,
            admit_target_thread=admit_target_thread,
            prompt_source_thread=prompt_source_thread,
            prompt_bind_mode=prompt_bind_mode,
            prompt_turn_number=prompt_turn_number,
            has_explicit_prompt_source=has_explicit_prompt_source,
            loop_closure=classification.loop_closure,
            allowlisted_silent=classification.allowlisted_silent,
            would_have_refused=classification.would_have_refused,
            would_have_refused_total=total,
            reason=classification.reason,
            spawn_uses_latest_on_thread=classification.spawn_uses_latest_on_thread,
            refused=refused,
        )
    )
    return True


def emit_loop_closure_admission(
    *,
    request_id: str | None,
    execution_id: str | None,
    admit_target_thread: str,
    prompt_source_thread: str,
    prompt_bind_mode: str | None,
    prompt_turn_number: int | None,
    has_explicit_prompt_source: bool,
    refused: bool = False,
) -> bool:
    """Emit loop_closure from generate-prepare B.3 refuse, before handoff.created."""
    return _emit_loop_closure_signal(
        request_id=request_id,
        execution_id=execution_id,
        admit_target_thread=admit_target_thread,
        prompt_source_thread=prompt_source_thread,
        prompt_bind_mode=prompt_bind_mode,
        prompt_turn_number=prompt_turn_number,
        has_explicit_prompt_source=has_explicit_prompt_source,
        refused=refused,
    )


async def _post_coord_turn(
    *,
    coord_thread_id: str,
    to_agent: str,
    from_agent: str,
    subject: str,
    body: str,
) -> None:
    if not _bus_token_configured() or not coord_thread_id:
        return
    payload: dict[str, Any] = {
        "thread": coord_thread_id,
        "from": from_agent,
        "to": to_agent,
        "subject": subject,
        "body": body,
        "status": "open",
    }
    try:
        async with make_async_client(DEFAULT_AGENT_BUS_URL, timeout=10.0) as client:
            await client.post("/turns", json=payload, headers=_bus_headers())
    except httpx.HTTPError as exc:
        logger.warning(
            "coord-thread notify failed: thread=%s subject=%s err=%s",
            coord_thread_id,
            subject,
            exc,
        )


async def post_coord_admit_pointer(
    *,
    coord_thread_id: str | None,
    worker_thread_id: str,
    to_agent: str,
    caller_agent: str | None,
    contract: str,
    request_id: str | None = None,
    execution_id: str | None = None,
    prompt_source_thread: str | None = None,
    prompt_bind_mode: str | None = None,
    prompt_turn_number: int | None = None,
    has_explicit_prompt_source: bool = False,
) -> None:
    if not coord_thread_id or coord_thread_id == worker_thread_id:
        return
    if prompt_source_thread:
        loop = _emit_loop_closure_signal(
            request_id=request_id,
            execution_id=execution_id,
            admit_target_thread=coord_thread_id,
            prompt_source_thread=prompt_source_thread,
            prompt_bind_mode=prompt_bind_mode,
            prompt_turn_number=prompt_turn_number,
            has_explicit_prompt_source=has_explicit_prompt_source,
            refused=True,
        )
        if loop:
            return
    await _post_coord_turn(
        coord_thread_id=coord_thread_id,
        to_agent=to_agent,
        from_agent=caller_agent or "dispatch",
        subject=_ADMIT_SUBJECT,
        body=_admit_body(worker_thread_id=worker_thread_id, contract=contract),
    )
