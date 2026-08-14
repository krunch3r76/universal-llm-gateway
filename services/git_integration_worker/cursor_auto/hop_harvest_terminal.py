"""Append-only hop harvest terminals — generate proof/stall, never admit.

Cadence watches already revoke or confirm on ``cdp.generate.stalled`` /
``cdp.generate.proof``. This module posts the bus-facing hop job terminal
that those watch rows used to leave silent. Turns are appended; a prior
``status:done`` or ``status:armed`` admit turn is not rewritten
(history-integrity bind on ``todo:hop-terminal-vs-successor-liveness``).
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Mapping
from typing import Any

from transport_utils import DEFAULT_AGENT_BUS_URL, make_sync_client
from universal_logging import get_logger

logger = get_logger(__name__)

_FROM_AUTO = "cursor-auto"
_TO_SUCCESSOR = "web-anthropic"

HarvestPoster = Callable[[str, str, str], None]


def default_harvest_poster(thread_id: str, subject: str, body: str) -> None:
    """POST one harvest terminal turn onto *thread_id* via the agent-bus UDS."""
    token = os.environ.get("AGENT_BUS_TOKEN", "").strip()
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    payload = {
        "thread": thread_id,
        "from": _FROM_AUTO,
        "to": _TO_SUCCESSOR,
        "subject": subject,
        "body": body,
        "status": "open",
    }
    with make_sync_client(DEFAULT_AGENT_BUS_URL, timeout=15.0) as client:
        client.post("/turns", json=payload, headers=headers)


def build_harvest_failed_turn(
    *,
    thread_id: str,
    execution_id: str,
    error: str | None,
    stall_stage: str | None = None,
    successor_birth_id: str | None = None,
) -> tuple[str, str]:
    """Return ``(subject, body)`` for a generate-harvest failure terminal.

    Subject carries ``status:failed`` so waiters and tip-readers see the
    harvest outcome, not Stargate admit. ``successor_birth_id`` is echoed
    as a minted key, not a seated successor.
    """
    err = (error or "").strip() or "generate harvest failed"
    subject = (
        f"status:failed — cursor-auto hop cadence — generate harvest failed "
        f"thread={thread_id}"
    )
    payload: dict[str, Any] = {
        "summary": f"continuity hop generate harvest failed: {err}",
        "reason": "continuity_hop_generate_harvest_failed",
        "continuity_hop": True,
        "execution_id": execution_id,
        "error": err,
        "disposition": "failed",
        "hop_phase": "harvest_failed",
        "history_integrity": "append",
    }
    if stall_stage:
        payload["stall_stage"] = stall_stage
    birth = (successor_birth_id or "").strip()
    if birth:
        payload["successor_birth_id"] = birth
        payload["successor_seated"] = False
    return subject, json.dumps(payload, indent=2)


def build_harvest_ok_turn(
    *,
    thread_id: str,
    execution_id: str,
    successor_birth_id: str | None = None,
) -> tuple[str, str]:
    """Return ``(subject, body)`` for a generate-harvest success terminal.

    ``dispatched-and-relayed`` is legal only here — generate proof, not
    Stargate ``execution_id`` mint.
    """
    subject = (
        f"status:done — cursor-auto hop cadence — generate harvest ok "
        f"thread={thread_id}"
    )
    payload: dict[str, Any] = {
        "summary": "continuity hop generate harvest ok",
        "reason": "continuity_hop_generate_harvest_ok",
        "continuity_hop": True,
        "execution_id": execution_id,
        "disposition": "dispatched-and-relayed",
        "hop_phase": "harvest_ok",
        "history_integrity": "append",
    }
    birth = (successor_birth_id or "").strip()
    if birth:
        payload["successor_birth_id"] = birth
    return subject, json.dumps(payload, indent=2)


def post_harvest_terminal_for_action(
    action: str,
    *,
    thread_id: str,
    row: Mapping[str, Any],
    payload: Mapping[str, Any],
    poster: HarvestPoster | None = None,
) -> dict[str, Any] | None:
    """Append the harvest terminal that matches a stall-reconcile *action*.

    ``revoked`` → ``status:failed``. ``confirmed`` (from ``cdp.generate.proof``)
    → ``status:done`` + ``dispatched-and-relayed``. Other actions are silent.
    Transport errors are logged and must not crash the cadence loop.
    """
    if action not in {"revoked", "confirmed"}:
        return None
    exec_id = str(payload.get("execution_id") or "").strip()
    if not exec_id:
        revoke = row.get("last_revoke")
        if isinstance(revoke, dict):
            exec_id = str(revoke.get("execution_id") or "").strip()
    birth = str(row.get("successor_birth_id") or "").strip() or None
    if action == "revoked":
        subject, body = build_harvest_failed_turn(
            thread_id=thread_id,
            execution_id=exec_id,
            error=str(payload.get("error") or "") or None,
            stall_stage=str(payload.get("stall_stage") or "") or None,
            successor_birth_id=birth,
        )
    else:
        subject, body = build_harvest_ok_turn(
            thread_id=thread_id,
            execution_id=exec_id,
            successor_birth_id=birth,
        )
    try:
        (poster or default_harvest_poster)(thread_id, subject, body)
    except Exception as exc:  # noqa: BLE001 — cadence must not crash on bus post
        logger.warning(
            "hop harvest terminal post failed thread=%s action=%s: %s",
            thread_id,
            action,
            exc,
        )
        return {"ok": False, "action": action, "error": str(exc)}
    return {"ok": True, "action": action, "subject": subject, "body": body}
