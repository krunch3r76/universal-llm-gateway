"""Async agent-bus HTTP client for the charter runner.

Manage runs in a separate process from the agent-bus service, so the runner
talks to the bus over its socket (the service of record) rather than opening the
SQLite file directly — this avoids DB-path coupling and concurrent-writer hazards.
Auth matches the MCP relay: ``Authorization: Bearer $AGENT_BUS_TOKEN``.
"""

from __future__ import annotations

import json
import os
from typing import Any

from transport_utils import DEFAULT_AGENT_BUS_URL, make_async_client

from .eligibility import ADMISSION_SUBJECT_PREFIX, ENROLLMENT_TAG

_TIMEOUT_S = 15.0
_TURN_FETCH_LIMIT = 10_000
_RUNNER_AGENT = "charter-runner"


def _auth_headers() -> dict[str, str]:
    token = os.environ.get("AGENT_BUS_TOKEN", "").strip()
    return {"Authorization": f"Bearer {token}"} if token else {}


async def list_enrolled_roots() -> list[dict[str, Any]]:
    """Active threads opted into the runner via the enrollment tag."""
    async with make_async_client(DEFAULT_AGENT_BUS_URL, timeout=_TIMEOUT_S) as client:
        resp = await client.get(
            "/threads",
            params=[("status", "active"), ("tags", ENROLLMENT_TAG)],
            headers=_auth_headers(),
        )
        resp.raise_for_status()
        return list(resp.json().get("threads", []))


async def fetch_turns(root_id: str) -> list[dict[str, Any]]:
    """All turns for a root (bodies included), for checkpoint scanning."""
    async with make_async_client(DEFAULT_AGENT_BUS_URL, timeout=_TIMEOUT_S) as client:
        resp = await client.get(
            "/turns",
            params={"thread": root_id, "last": _TURN_FETCH_LIMIT, "compact": "false"},
            headers=_auth_headers(),
        )
        resp.raise_for_status()
        return list(resp.json().get("turns", []))


async def post_admission_pointer(
    root_id: str,
    *,
    window_index: int,
    posted_at_iso: str,
    worker_thread: str = "",
    packet_path: str = "",
) -> dict[str, Any]:
    """Post the in-flight marker turn on the root (after a successful handoff).

    A later CHECKPOINT past this turn clears the in-flight state; a soft
    ``waiting_open`` remind (not auto-fail) covers operator-open latency.
    ``worker_thread`` / ``packet_path`` feed the /tmp transcript closeout harvest.
    """
    meta = {
        "charter_runner": True,
        "window": window_index,
        "posted_at": posted_at_iso,
        "worker_thread": worker_thread,
        "packet_path": packet_path,
    }
    body = json.dumps(meta, separators=(",", ":"))
    payload = {
        "thread": root_id,
        "from": _RUNNER_AGENT,
        "to": "cursor",
        "subject": f"{ADMISSION_SUBJECT_PREFIX} window {window_index}",
        "body": body,
    }
    async with make_async_client(DEFAULT_AGENT_BUS_URL, timeout=_TIMEOUT_S) as client:
        resp = await client.post("/threads/send", json=payload, headers=_auth_headers())
        resp.raise_for_status()
        return dict(resp.json())


async def close_worker_thread(
    worker_thread: str, *, summary: str
) -> dict[str, Any]:
    """Close a finished handoff window thread (root stays open)."""
    payload = {
        "summary": summary,
        "mark_all_read": True,
    }
    async with make_async_client(DEFAULT_AGENT_BUS_URL, timeout=_TIMEOUT_S) as client:
        resp = await client.patch(
            f"/threads/{worker_thread}/close",
            json=payload,
            headers=_auth_headers(),
        )
        resp.raise_for_status()
        return dict(resp.json())


async def fetch_thread(thread_id: str) -> dict[str, Any]:
    """Fetch one thread detail (status, summary, …)."""
    async with make_async_client(DEFAULT_AGENT_BUS_URL, timeout=_TIMEOUT_S) as client:
        resp = await client.get(
            f"/threads/{thread_id}",
            headers=_auth_headers(),
        )
        resp.raise_for_status()
        return dict(resp.json())


def closeout_status_from_turns(turns: list[dict[str, Any]]) -> str | None:
    """Latest machine-closeout ``status`` from worker turns, if present.

    Cursor-sdk / dispatch closeouts post a JSON body with a ``status`` field
    (``complete`` | ``partial`` | ``failed`` | ``timeout``). Newest turn wins.
    """
    ordered = sorted(
        turns,
        key=lambda t: int(t.get("turn_number") or 0),
        reverse=True,
    )
    for turn in ordered:
        body = str(turn.get("body") or "").strip()
        if not body.startswith("{"):
            continue
        try:
            data = json.loads(body)
        except (ValueError, TypeError):
            continue
        if isinstance(data, dict) and "status" in data:
            return str(data["status"]).strip().lower()
    return None


async def worker_failure_reason(worker_thread: str) -> str | None:
    """Return a failure reason if the worker closeout/thread is terminal-failed.

    - Closeout body ``status ∈ {failed, timeout}`` → that status.
    - Thread ``status == closed`` with no successful closeout → ``worker_closed``.
    - Otherwise ``None`` (still running / completed successfully).
    """
    if not worker_thread:
        return None
    turns = await fetch_turns(worker_thread)
    status = closeout_status_from_turns(turns)
    if status in {"failed", "timeout"}:
        return status
    detail = await fetch_thread(worker_thread)
    thread_status = str(detail.get("status") or "").lower()
    if thread_status == "closed" and status not in {"complete", "partial"}:
        return "worker_closed"
    return None
