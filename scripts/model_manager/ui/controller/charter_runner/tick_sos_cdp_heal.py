"""CDP operator-proxy heal submit for charter-tick SOS.

Fires ``POST /api/v1/team/dispatch`` with ``model=cdp/opus-5`` and
``purpose=operator-proxy`` so mission skill chips ride the CDP generate path
(``decision:project-ask-escape-only-teaching-align`` Class-F).
"""

from __future__ import annotations

from typing import Any

from transport_utils import DEFAULT_STARGATE_URL, make_async_client
from universal_logging import get_logger

logger = get_logger(__name__)

_TIMEOUT_S = 30.0
_DISPATCH_PATH = "/api/v1/team/dispatch"
_CALLER = "charter-runner"
_CDP_MODEL = "cdp/opus-5"


def build_heal_prompt(
    root_id: str,
    *,
    reason: str,
    consecutive: int,
    detail: str,
    fire_attempt_outcome: Any = None,
    worker_thread: str | None = None,
) -> str:
    """Compose the SOS heal DIRECTIVE body (mission ACs + outcome framing)."""
    from .root_health import FireAttemptOutcome

    outcome_val = (
        fire_attempt_outcome.value
        if isinstance(fire_attempt_outcome, FireAttemptOutcome)
        else str(fire_attempt_outcome or "")
    )
    heal_note = ""
    if fire_attempt_outcome == FireAttemptOutcome.FIRED_BOOKKEEPING_FAILED:
        heal_note = (
            "\n\n**CRITICAL:** `fired_bookkeeping_failed` — window already fired. "
            "Recover admission pointer / harvest only. Do NOT re-dispatch the window.\n"
            f"Bound worker: {worker_thread or '(unknown)'}\n"
        )
    elif fire_attempt_outcome in {
        FireAttemptOutcome.REFUSED_PRE_FIRE,
        FireAttemptOutcome.ERRORED_PRE_FIRE,
    }:
        heal_note = (
            "\n\nPre-fire refuse/error — re-fire may be safe after substrate fix.\n"
        )
    return (
        f"# DIRECTIVE — Charter tick SOS heal root {root_id}\n\n"
        "TYPE: DIRECTIVE\n"
        "so_what: ULG: heal charter-tick silent-starve — no Kaywan babysitting\n\n"
        f"root: agent-bus:{root_id}\n"
        f"fire_attempt_outcome: {outcome_val}\n"
        f"reason: {reason}\n"
        f"consecutive: {consecutive}\n"
        f"detail: {detail or '(none)'}\n"
        f"{heal_note}\n"
        "You hold the operator seat. Pull live tip + ledger + recent "
        "`manage.charter.tick.root_skipped` / harvest events. Heal so the belt "
        "progresses (admit/queue, tip amend, or honest blocked+friction). "
        "Prevention via friction/amend at your discretion. Commission cursor-auto "
        "for implement. Page COME TO IDE only for debrief / options exhausted.\n"
        "Doctrine: decision:tick-heal-cdp-operator-default\n"
        "purpose: operator-proxy\n"
    )


async def submit_cdp_heal(
    root_id: str,
    *,
    reason: str,
    consecutive: int,
    detail: str,
    fire_attempt_outcome=None,
    worker_thread: str | None = None,
) -> str | None:
    """Admit CDP generate heal via Stargate team dispatch; return execution_id."""
    prompt = build_heal_prompt(
        root_id,
        reason=reason,
        consecutive=consecutive,
        detail=detail,
        fire_attempt_outcome=fire_attempt_outcome,
        worker_thread=worker_thread,
    )
    body = {
        "op": "generate",
        "model": _CDP_MODEL,
        "contract": "light-bounded",
        "prompt": prompt,
        "purpose": "operator-proxy",
        "dispatch_thread_id": str(root_id),
        "caller_agent": _CALLER,
    }
    try:
        async with make_async_client(DEFAULT_STARGATE_URL, timeout=_TIMEOUT_S) as client:
            resp = await client.post(_DISPATCH_PATH, json=body)
            if resp.status_code >= 400:
                logger.warning(
                    "tick SOS CDP dispatch HTTP %s root=%s body=%r",
                    resp.status_code,
                    root_id,
                    (resp.text or "")[:200],
                )
                return None
            payload = resp.json()
    except Exception:  # noqa: BLE001 — SOS must not abort tick
        logger.exception("tick SOS CDP dispatch failed root=%s", root_id)
        return None
    if not isinstance(payload, dict):
        return None
    return str(payload.get("execution_id") or "") or None


__all__ = ["build_heal_prompt", "submit_cdp_heal"]
