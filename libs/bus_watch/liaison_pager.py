"""Liaison pager and seat-lease forfeit — the two operator-facing side effects of
the gear-3 ticker, kept out of the spawn predicate module.

``page_liaison`` posts to the email-bridge pager socket (Fi SMS relay; no
personal endpoint in code). ``maybe_forfeit_expired_lease`` is the designed
stop for a seat whose lease ran out without a release: emit the event, page
once, and clear the holder so the next poll does not text the same turn again.
A live holder is not a forfeit — the ticker calls this every poll.
"""

from __future__ import annotations

import json
import os
import subprocess
from typing import Any

from bus_watch.events import emit_lease_forfeited
from bus_watch.fable_lock import read_lock, release_fable_lock, seat_lock_free

_EMAIL_BRIDGE_SOCK = os.environ.get(
    "EMAIL_BRIDGE_SOCK", "/tmp/universal-protocol/email-bridge.sock"
)


def page_liaison(root_id: str, subject: str, body: str) -> dict[str, Any]:
    """Fire the email-bridge pager; returns ok=False when the socket is absent."""
    if not os.path.exists(_EMAIL_BRIDGE_SOCK):
        return {"ok": False, "reason": "pager_unavailable"}
    payload = json.dumps(
        {"subject": subject, "body": body, "tag": "liaison"},
        ensure_ascii=False,
    )
    proc = subprocess.run(
        [
            "curl",
            "-sS",
            "--unix-socket",
            _EMAIL_BRIDGE_SOCK,
            "-H",
            "Content-Type: application/json",
            "-d",
            payload,
            "http://localhost/pager/notify",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    return {"ok": proc.returncode == 0, "stdout": proc.stdout[:200]}


def maybe_forfeit_expired_lease(
    root_id: str,
    *,
    lock: dict[str, Any] | None = None,
    last_holder_turn: int | None = None,
) -> bool:
    """Page once and clear the seat when its lease has already elapsed.

    ``seat_lock_free`` is true both for an empty seat and for a holder past
    ``expires_at``. Only the second case is a forfeit. A live holder returns
    false with no SMS: the gear-3 poll used to take that branch and text the
    same ``last_holder_turn`` every ``poll_seconds`` while the IDE heartbeat
    kept sliding ``expires_at`` forward.

    Returns true after the forfeit. Side effects: ``liaison.lease.forfeited``,
    one pager post, and ``release_fable_lock`` so the next poll sees no holder.
    """
    lock = lock if lock is not None else read_lock(root_id)
    holder = str(lock.get("holder") or "")
    if not holder or not seat_lock_free(lock, root_id=root_id):
        return False
    emit_lease_forfeited(
        root_id=root_id,
        holder=holder,
        expires_at=str(lock.get("expires_at") or ""),
        last_holder_turn=last_holder_turn,
    )
    page_liaison(
        root_id,
        f"liaison {root_id} — lease forfeit",
        f"Holder {holder} lease expired at {lock.get('expires_at')}; "
        f"last holder turn={last_holder_turn}.",
    )
    release_fable_lock(holder, pid=None, root_id=root_id)
    return True


__all__ = ["maybe_forfeit_expired_lease", "page_liaison"]
