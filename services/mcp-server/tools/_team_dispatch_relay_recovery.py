"""Recover cursor-sdk generate admits hidden by MCP→Stargate transport loss (a:38605).

When ``_relay`` times out after Stargate already returned 202, the coord thread
carries an admit pointer and the worker thread's first turn scopes
``cursor-sdk:dispatch:{execution_id}``.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any

import httpx
from transport_utils import DEFAULT_AGENT_BUS_URL, make_async_client

_ADMIT_SUBJECT = "cursor-sdk generate admitted"
_WORKER_THREAD_RE = re.compile(r"Worker thread `(\d+)`")
_EXECUTION_IN_TO_RE = re.compile(
    r"cursor-sdk:dispatch:([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})",
    re.IGNORECASE,
)
_REQUEST_ID_IN_SUBJECT_RE = re.compile(
    r"cursor-sdk generate — ([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})",
    re.IGNORECASE,
)

_BUS_GET_TIMEOUT_S = 8.0
_CURSOR_SDK_REPLY_FROM = "cursor-sdk"


def _bus_headers() -> dict[str, str]:
    token = os.getenv("AGENT_BUS_TOKEN", "").strip()
    return {"Authorization": f"Bearer {token}"} if token else {}


def _turn_field(turn: dict[str, Any], key: str) -> str:
    val = turn.get(key)
    return str(val).strip() if val is not None else ""


def _parse_turns_payload(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [t for t in payload if isinstance(t, dict)]
    if isinstance(payload, dict):
        turns = payload.get("turns")
        if isinstance(turns, list):
            return [t for t in turns if isinstance(t, dict)]
    return []


def worker_thread_from_admit_body(body: str) -> str | None:
    match = _WORKER_THREAD_RE.search(body or "")
    return match.group(1) if match else None


def execution_id_from_turn(turn: dict[str, Any]) -> str | None:
    for key in ("to", "to_agent"):
        text = _turn_field(turn, key)
        match = _EXECUTION_IN_TO_RE.search(text)
        if match:
            return match.group(1)
    return None


def request_id_from_generate_subject(subject: str) -> str | None:
    match = _REQUEST_ID_IN_SUBJECT_RE.search(subject or "")
    return match.group(1) if match else None


def build_recovered_poll_hint(
    *,
    thread_id: str,
    execution_id: str,
    after_turn: int = 1,
) -> dict[str, Any]:
    wait_args = {
        "thread": thread_id,
        "after_turn": after_turn,
        "wait_seconds": 0,
        "completion": "first_reply_from",
        "from_agent": _CURSOR_SDK_REPLY_FROM,
        "execution_id": execution_id,
    }
    return {
        "tool": "wait",
        "arguments": wait_args,
        "arguments_json": json.dumps(wait_args, separators=(",", ":")),
    }


def build_recovered_generate_envelope(
    *,
    worker_thread_id: str,
    execution_id: str,
    request_id: str | None,
    dispatch_thread_id: str,
) -> dict[str, Any]:
    poll_hint = build_recovered_poll_hint(
        thread_id=worker_thread_id,
        execution_id=execution_id,
    )
    to_agent = f"cursor-sdk:dispatch:{execution_id}"
    envelope: dict[str, Any] = {
        "op": "generate",
        "substrate": "sdk",
        "execution_id": execution_id,
        "thread_id": worker_thread_id,
        "thread": worker_thread_id,
        "to_agent": to_agent,
        "dispatch_thread_id": dispatch_thread_id,
        "poll_hint": poll_hint,
        "result_handle": {
            "kind": "dual",
            "execution_id": execution_id,
            "thread_id": worker_thread_id,
            "substrate": "sdk",
            "durable": True,
        },
        "recovered_after_relay_transport_error": True,
    }
    if request_id:
        envelope["request_id"] = request_id
    return envelope


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
                "Stargate may have admitted despite the lost HTTP response. "
                "Read the coordination thread for subject "
                f"'{_ADMIT_SUBJECT}' (thread {dispatch_thread_id}) or the worker "
                "thread tip before retrying the same work_key."
            ),
        }
    }


def _cursor_sdk_generate_body(body: dict[str, Any]) -> bool:
    if body.get("op") != "generate":
        return False
    seat = (body.get("seat") or "").strip()
    role = (body.get("role") or "").strip()
    return seat == "cursor-sdk" or role == "cursor-sdk"


async def _fetch_turns(client: httpx.AsyncClient, thread_id: str, *, last: int) -> list[dict[str, Any]]:
    resp = await client.get(
        f"/turns?thread={thread_id}&last={last}",
        headers=_bus_headers(),
    )
    if resp.status_code >= 400:
        return []
    try:
        return _parse_turns_payload(resp.json())
    except ValueError:
        return []


async def recover_team_dispatch_after_transport_error(
    *,
    body: dict[str, Any],
    exc: BaseException,
) -> dict[str, Any] | None:
    """Return a 202-shaped admit envelope when bus evidence shows an admit landed."""
    if not _cursor_sdk_generate_body(body):
        return None
    dispatch_thread_id = str(body.get("dispatch_thread_id") or "").strip()
    if not dispatch_thread_id:
        return None

    try:
        async with make_async_client(
            DEFAULT_AGENT_BUS_URL, timeout=_BUS_GET_TIMEOUT_S
        ) as client:
            coord_turns = await _fetch_turns(client, dispatch_thread_id, last=8)
            worker_thread_id: str | None = None
            for turn in reversed(coord_turns):
                if _turn_field(turn, "subject") != _ADMIT_SUBJECT:
                    continue
                worker_thread_id = worker_thread_from_admit_body(
                    _turn_field(turn, "body")
                )
                if worker_thread_id:
                    break
            if not worker_thread_id:
                return None

            worker_turns = await _fetch_turns(client, worker_thread_id, last=3)
            if not worker_turns:
                return None
            worker_turn = worker_turns[-1]
            execution_id = execution_id_from_turn(worker_turn)
            if not execution_id:
                return None
            request_id = request_id_from_generate_subject(
                _turn_field(worker_turn, "subject")
            )
    except httpx.HTTPError:
        return None

    return build_recovered_generate_envelope(
        worker_thread_id=worker_thread_id,
        execution_id=execution_id,
        request_id=request_id,
        dispatch_thread_id=dispatch_thread_id,
    )


def transport_error_code(exc: BaseException, *, recovered: bool) -> str:
    if recovered:
        return "recovered"
    if isinstance(exc, httpx.ConnectError):
        return "stargate_unreachable"
    if isinstance(exc, httpx.TimeoutException):
        return "stargate_response_lost"
    return "stargate_unreachable"
