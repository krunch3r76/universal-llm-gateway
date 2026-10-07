"""Honest transport-error codes for team_dispatch relay (a:38605, CDP bind A).

Unkeyed bus scraping was removed after review agent-bus:15603#2 — stale coord
pointers returned wrong execution_ids. Callers on ``stargate_response_lost``
read the dispatch thread newest-first before retrying the same work_key.
"""

from __future__ import annotations

from typing import Any

import httpx

_ADMIT_SUBJECT = "cursor-sdk generate admitted"
_GENERATE_SUBJECT_PREFIX = "cursor-sdk generate —"


def build_response_lost_error(
    *,
    exc: BaseException,
    dispatch_thread_id: str,
) -> dict[str, Any]:
    name = type(exc).__name__
    text = str(exc).strip()
    message = f"{name}: {text}" if text else name
    return {
        "error": {
            "code": "stargate_response_lost",
            "message": message,
            "dispatch_thread_id": dispatch_thread_id,
            "fix_hint": (
                "The HTTP response may have been lost after Stargate admitted. "
                f"Read agent-bus thread {dispatch_thread_id} newest-first: look for "
                f"subject '{_GENERATE_SUBJECT_PREFIX}…' on that thread, or "
                f"'{_ADMIT_SUBJECT}' pointing at a worker thread, only for turns "
                "created after this call. Do not retry the same work_key until "
                "you have execution_id and poll_hint from that read."
            ),
        }
    }


def transport_error_code(exc: BaseException) -> str:
    """Map relay transport failures to caller-facing error codes."""
    if isinstance(exc, httpx.ConnectError):
        return "stargate_unreachable"
    if isinstance(exc, httpx.TimeoutException):
        return "stargate_response_lost"
    if isinstance(exc, httpx.RemoteProtocolError):
        return "stargate_response_lost"
    return "stargate_unreachable"
