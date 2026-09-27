"""Decide whether a waited bus turn is the consult closeout.

A watcher waking on a turn is not a closeout. Cowork posts progress
("Ran a command, loaded tools") as a ``cdp reply`` before the answer.
``proof_reply_from`` on a stale agent_bus still completes on that turn;
the seat-side watcher re-checks the body and keeps polling.
"""

from __future__ import annotations

from typing import Any

from chat_harvest.chrome import substantive_reply_body


def fetch_turn_body(client: Any, thread_id: str, turn: int) -> str:
    """Return the turn body from agent-bus. Empty when the payload has none."""
    resp = client.get(
        "http://localhost/turns/by-number",
        params={"thread": thread_id, "turn_number": str(turn)},
    )
    resp.raise_for_status()
    payload = resp.json()
    if not isinstance(payload, dict):
        return ""
    nested = payload.get("turn")
    row = nested if isinstance(nested, dict) else payload
    return str(row.get("body") or "")


def closeout_after_turn(body: str, *, turn: int, after_turn: int) -> tuple[bool, int]:
    """Return ``(is_closeout, after_turn)``.

    A non-substantive body advances ``after_turn`` to ``turn`` so the next
    poll does not treat the same progress post as the reply.
    """
    if substantive_reply_body(body or ""):
        return True, after_turn
    return False, max(after_turn, turn)
