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


def is_admit_pending_transport(exc: BaseException) -> bool:
    """Transport failures where generate may have been admitted server-side."""
    return isinstance(exc, (httpx.ReadTimeout, httpx.RemoteProtocolError))


def build_admit_pending_response(
    *,
    exc: BaseException,
    dispatch_id: str,
    work_key: str | None,
    dispatch_thread_id: str,
) -> dict[str, Any]:
    """Honest pending envelope when the HTTP response was lost on generate."""
    wk = str(work_key or "").strip()
    read_hints: list[str] = []
    if wk:
        read_hints.append(
            f"team_dispatch(op=registry, work_key_prefix={wk}, state=all)"
        )
    read_hints.append(
        f'agent_bus thread {dispatch_thread_id} subject "cursor-sdk generate —"'
    )
    return {
        "status": "admit_pending",
        "dispatch_id": dispatch_id,
        "work_key": wk or None,
        "dispatch_thread_id": dispatch_thread_id,
        "do_not_refire": True,
        "read": read_hints,
        "poll_after_s": 30,
        "transport": type(exc).__name__,
    }
