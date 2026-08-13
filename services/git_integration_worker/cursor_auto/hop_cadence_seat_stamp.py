"""Post ``TYPE: SEAT_REGISTRATION`` when hop-cadence observes a new registration.

The stamp is a projection of the CDP registry (Invariant 2). The I6 key on
it is ``successor_birth_id`` copied from the structural hop body, not
``chat_url``. Fail-closed: a transport error must not break confirm.
"""

from __future__ import annotations

import os
from collections.abc import Callable

from hop_handoff import build_seat_registration_stamp
from transport_utils import DEFAULT_AGENT_BUS_URL, make_sync_client
from universal_logging import get_logger

logger = get_logger(__name__)

_FROM_AUTO = "cursor-auto"

StampPoster = Callable[[str, str], None]


def default_stamp_poster(thread_id: str, body: str) -> None:
    """POST the stamp turn onto *thread_id* via the agent-bus UDS."""
    token = os.environ.get("AGENT_BUS_TOKEN", "").strip()
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    payload = {
        "thread": thread_id,
        "from": _FROM_AUTO,
        "to": "web-anthropic",
        "subject": f"TYPE: SEAT_REGISTRATION — thread {thread_id}",
        "body": body,
        "status": "open",
    }
    with make_sync_client(DEFAULT_AGENT_BUS_URL, timeout=15.0) as client:
        client.post("/turns", json=payload, headers=headers)


def post_seat_registration_if_keyed(
    *,
    thread_id: str,
    successor_birth_id: str | None,
    registration_id: str,
    execution_id: str,
    chat_url: str | None,
    stamp_poster: StampPoster | None = None,
) -> str | None:
    """Build and post the stamp when a birth id is on the watch row.

    Returns the stamp body when posted, else None (no key → no stamp).
    """
    birth = (successor_birth_id or "").strip()
    if not birth:
        return None
    stamp = build_seat_registration_stamp(
        successor_birth_id=birth,
        registration_id=registration_id,
        execution_id=execution_id,
        parent_thread=thread_id,
        chat_url=chat_url,
    )
    poster = stamp_poster or default_stamp_poster
    try:
        poster(thread_id, stamp)
    except Exception as exc:  # noqa: BLE001 — confirm must not crash on stamp
        logger.warning(
            "seat_registration stamp post failed thread=%s: %s", thread_id, exc
        )
        return stamp
    return stamp
