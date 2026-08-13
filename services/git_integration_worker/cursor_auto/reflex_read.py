"""Run the bounded premium second-read leg between executor terminal and relay.

Sits in the one seam where a cheap independent read changes what happens next:
the executor has finished and the manager has not yet been told anything. A
failure anywhere in here is non-fatal by construction — the closeout still ships,
just without the extra field. Never let an advisory leg gate a real result.

Deliberately blocking rather than fire-and-forget: the value is the manager
reading one artifact, so a second read that arrives after the CLOSEOUT it was
meant to qualify is worth close to nothing. The cost of that choice is bounded
latency, which is what the timeout and the firing predicate are for. The reflex
also posts its own cursor-sdk turn on the request thread; that lands before the
Auto CLOSEOUT, so waiters keyed on ``status:done`` are unaffected.
"""

from __future__ import annotations

import asyncio
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from cursor_capabilities import canonical_cursor_bare_id, supported_knobs
from universal_logging import get_logger

from services.git_integration_worker.cursor_auto.queue import AutoJob
from services.git_integration_worker.cursor_auto.reflex_events import (
    emit_second_read,
    maybe_emit_premium_bind,
)
from services.git_integration_worker.cursor_auto.reflex_packet import (
    build_reflex_packet,
    parse_second_read,
)
from services.git_integration_worker.cursor_auto.reflex_policy import (
    counters,
    evaluate_reflex,
)
from services.git_integration_worker.cursor_auto.wire_map import compose_model_knobs
from services.git_integration_worker.cursor_bus import CursorBusClient

logger = get_logger(__name__)

# Roaming-tier default: the reflex is a short read-only closeout check, not a
# bind-leg judgment seat. Opus remains available via CURSOR_AUTO_REFLEX_MODEL.
_DEFAULT_MODEL = "cursor/grok-4.6"
_DEFAULT_EFFORT = "low"
# The reflex sits between the executor finishing and the manager being told
# anything, so every second here is latency the manager pays. A bounded read of
# one closeout at effort=low lands well inside this; the cap exists to bound the
# pathological case, not to accommodate a slow one.
_DEFAULT_TIMEOUT_S = 180.0
# A reflex that reads a closeout and a handful of cited files does not need the
# wide context window, and the narrow one is materially cheaper per call.
_LEAN_BASE_KNOBS = {"thinking": "true", "context": "300k"}
_TERMINAL_STATUSES = frozenset({"completed", "failed"})
_POLL_INTERVAL_S = 2.0


@dataclass(frozen=True, slots=True)
class ReflexOutcome:
    """A completed second read, ready to inject into the relay body."""

    text: str
    model: str
    dispatch_id: str
    reason: str


def reflex_model() -> str:
    return os.environ.get("CURSOR_AUTO_REFLEX_MODEL", "").strip() or _DEFAULT_MODEL


def reflex_effort() -> str:
    return (
        os.environ.get("CURSOR_AUTO_REFLEX_EFFORT", "").strip().lower()
        or _DEFAULT_EFFORT
    )


def reflex_timeout_s() -> float:
    raw = os.environ.get("CURSOR_AUTO_REFLEX_TIMEOUT_S", "").strip()
    if not raw:
        return _DEFAULT_TIMEOUT_S
    try:
        return max(30.0, float(raw))
    except ValueError:
        return _DEFAULT_TIMEOUT_S


def reflex_knobs(model_id: str) -> dict[str, str]:
    """Lean knobs the target model actually accepts, with reflex effort merged."""
    try:
        bare = canonical_cursor_bare_id(model_id)
    except ValueError:
        return {}
    accepted = supported_knobs(bare)
    base = {name: value for name, value in _LEAN_BASE_KNOBS.items() if name in accepted}
    return compose_model_knobs(
        {"resolved_model_id": model_id, "model_knobs": base},
        {"resolved_effort": reflex_effort()},
    )


async def maybe_run_second_read(
    job: AutoJob,
    *,
    contract: str,
    terminal_status: str,
    sdk_body: str | None,
    executor_model: str,
    executor_dispatch_id: str,
    density: str | None = None,
    bus: CursorBusClient | None = None,
    superseded: Callable[[], bool] | None = None,
) -> ReflexOutcome | None:
    """Fire a bounded premium read of the executor's closeout when triggers fire.

    Returns ``None`` whenever the reflex does not fire or does not land, which
    the caller must treat as ordinary — not as a failure of the episode.
    """
    try:
        return await _maybe_run_second_read(
            job,
            contract=contract,
            terminal_status=terminal_status,
            sdk_body=sdk_body,
            executor_model=executor_model,
            executor_dispatch_id=executor_dispatch_id,
            density=density,
            bus=bus,
            superseded=superseded,
        )
    except Exception as exc:  # noqa: BLE001 — advisory leg must never fail a relay
        # The caller's next statement relays the executor's real closeout. Anything
        # raised here would abort that, which is the one outcome this leg may not
        # have: an optional field is never worth a lost result.
        logger.warning(
            "cursor-auto second read raised, relaying without it thread=%s: %s",
            job.thread_id,
            exc,
        )
        emit_second_read(
            thread_id=job.thread_id,
            executor_dispatch_id=executor_dispatch_id,
            reflex_dispatch_id=None,
            fired=True,
            reason="exception",
            model=None,
            contract=contract,
            outcome=f"exception:{type(exc).__name__}",
        )
        return None


async def _maybe_run_second_read(
    job: AutoJob,
    *,
    contract: str,
    terminal_status: str,
    sdk_body: str | None,
    executor_model: str,
    executor_dispatch_id: str,
    density: str | None,
    bus: CursorBusClient | None,
    superseded: Callable[[], bool] | None,
) -> ReflexOutcome | None:
    """Unguarded body of :func:`maybe_run_second_read` — callers must wrap."""
    verdict = evaluate_reflex(
        thread_id=job.thread_id,
        contract=contract,
        terminal_status=terminal_status,
        sdk_body=sdk_body,
        density=density,
    )
    if not verdict.fire:
        emit_second_read(
            thread_id=job.thread_id,
            executor_dispatch_id=executor_dispatch_id,
            reflex_dispatch_id=None,
            fired=False,
            reason=verdict.reason,
            model=None,
            contract=contract,
        )
        return None
    if superseded is not None and superseded():
        emit_second_read(
            thread_id=job.thread_id,
            executor_dispatch_id=executor_dispatch_id,
            reflex_dispatch_id=None,
            fired=False,
            reason="superseded_before_fire",
            model=None,
            contract=contract,
        )
        return None

    model_id = reflex_model()
    return await _run_reflex_dispatch(
        job,
        contract=contract,
        model_id=model_id,
        knobs=reflex_knobs(model_id),
        sdk_body=sdk_body,
        executor_model=executor_model,
        executor_dispatch_id=executor_dispatch_id,
        bus=bus,
        superseded=superseded,
        reason=verdict.reason,
    )


async def _poll_reflex_terminal(
    *,
    dispatch_id: str,
    timeout_s: float,
    superseded: Callable[[], bool] | None,
) -> dict[str, Any]:
    """Poll this leg's own ledger row until terminal.

    Deliberately not the shared by-thread poller: that one reads only the newest
    row for the thread, so a sibling admitting after this leg would hide its
    terminal until the timeout expired — and every second spent there is a second
    the executor's finished closeout is not yet relayed.
    """
    from services.git_integration_worker.cursor_dispatch_ledger import (
        CursorDispatchLedger,
    )

    ledger = CursorDispatchLedger.instance()
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if superseded is not None and superseded():
            return {"terminal": False, "reason": "superseded"}
        row = await asyncio.to_thread(
            ledger.dispatch_status_by_id, dispatch_id=dispatch_id
        )
        if row is not None and row.get("status") in _TERMINAL_STATUSES:
            return {"terminal": True, "status": row["status"], "row": row}
        await asyncio.sleep(_POLL_INTERVAL_S)
    return {"terminal": False, "reason": "dispatch_poll_timeout"}


async def _run_reflex_dispatch(
    job: AutoJob,
    *,
    contract: str,
    model_id: str,
    knobs: dict[str, str],
    sdk_body: str | None,
    executor_model: str,
    executor_dispatch_id: str,
    bus: CursorBusClient | None,
    superseded: Callable[[], bool] | None,
    reason: str,
) -> ReflexOutcome | None:
    """Submit, poll, and parse one reflex dispatch; ``None`` on any shortfall."""
    from services.git_integration_worker.cursor_auto.nested_sdk import (
        fetch_sdk_closeout_body,
        submit_nested_dispatch,
    )

    message = build_reflex_packet(
        directive_body=job.body,
        closeout_body=sdk_body,
        executor_model=executor_model,
        contract=contract,
        executor_dispatch_id=executor_dispatch_id,
    )
    submit = await submit_nested_dispatch(
        job,
        model_id=model_id,
        handoff_contract="light-bounded",
        message=message,
        # No nest_under: read_only legs are lease-exempt, so parking under the
        # executor's parent would re-park a live holder for this leg's duration.
        nest_under=None,
        model_knobs=knobs or None,
        read_only=True,
    )
    reflex_dispatch_id = str(submit.get("dispatch_id") or "")
    maybe_emit_premium_bind(
        thread_id=job.thread_id,
        dispatch_id=reflex_dispatch_id,
        model=model_id,
        handoff_contract="light-bounded",
        lane="cursor-auto-reflex",
        knobs=knobs,
    )
    if not submit.get("ok"):
        logger.warning(
            "cursor-auto reflex submit failed thread=%s error=%s",
            job.thread_id,
            submit.get("error"),
        )
        _emit_outcome(job, executor_dispatch_id, reflex_dispatch_id, model_id,
                      contract, reason, "submit_failed")
        return None
    # Charge the episode budget only once a leg actually exists — a refused admit
    # costs nothing and should not consume a thread's allowance of second reads.
    counters().note_spend(job.thread_id)

    polled = await _poll_reflex_terminal(
        dispatch_id=reflex_dispatch_id,
        timeout_s=reflex_timeout_s(),
        superseded=superseded,
    )
    if not polled.get("terminal"):
        _emit_outcome(job, executor_dispatch_id, reflex_dispatch_id, model_id,
                      contract, reason, str(polled.get("reason") or "not_terminal"))
        return None

    body = await fetch_sdk_closeout_body(
        thread_id=job.thread_id,
        dispatch_id=reflex_dispatch_id,
        bus=bus,
    )
    answer = parse_second_read(body)
    if not answer:
        _emit_outcome(job, executor_dispatch_id, reflex_dispatch_id, model_id,
                      contract, reason, "unparseable")
        return None

    _emit_outcome(job, executor_dispatch_id, reflex_dispatch_id, model_id,
                  contract, reason, "delivered")
    return ReflexOutcome(
        text=answer,
        model=model_id,
        dispatch_id=reflex_dispatch_id,
        reason=reason,
    )


def _emit_outcome(
    job: AutoJob,
    executor_dispatch_id: str,
    reflex_dispatch_id: str,
    model_id: str,
    contract: str,
    reason: str,
    outcome: str,
) -> None:
    emit_second_read(
        thread_id=job.thread_id,
        executor_dispatch_id=executor_dispatch_id,
        reflex_dispatch_id=reflex_dispatch_id or None,
        fired=True,
        reason=reason,
        model=model_id,
        contract=contract,
        outcome=outcome,
    )


__all__ = [
    "ReflexOutcome",
    "maybe_run_second_read",
    "reflex_effort",
    "reflex_knobs",
    "reflex_model",
    "reflex_timeout_s",
]
