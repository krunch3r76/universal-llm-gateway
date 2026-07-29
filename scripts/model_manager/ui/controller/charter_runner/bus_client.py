"""Async agent-bus HTTP client for the charter runner.

Manage runs in a separate process from the agent-bus service, so the runner
talks to the bus over its socket (the service of record) rather than opening the
SQLite file directly — this avoids DB-path coupling and concurrent-writer hazards.
Auth matches the MCP relay: ``Authorization: Bearer $AGENT_BUS_TOKEN``.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from typing import Any

from transport_utils import DEFAULT_AGENT_BUS_URL, make_async_client

from .admission import ADMISSION_SUBJECT_PREFIX, ENROLLMENT_TAG

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
    admission_mode: str = "",
) -> dict[str, Any]:
    """Post the in-flight marker turn on the root (after a successful handoff).

    A later CHECKPOINT past this turn clears the in-flight state; a soft
    ``waiting_open`` remind (not auto-fail) covers operator-open latency.
    ``worker_thread`` / ``packet_path`` feed the /tmp transcript closeout harvest.
    ``admission_mode`` is the mode armed at fire time (self-heal reads this, not
    the live env, so mid-arc mode flips cannot heal an attended window).
    """
    meta = {
        "charter_runner": True,
        "window": window_index,
        "posted_at": posted_at_iso,
        "worker_thread": worker_thread,
        "packet_path": packet_path,
        "admission_mode": admission_mode,
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


async def post_root_turn(
    root_id: str,
    *,
    subject: str,
    body: str,
    to: str = "charter-runner",
) -> dict[str, Any]:
    """Post an arbitrary turn on the charter root (NOTE / CHECKPOINT / etc.)."""
    payload = {
        "thread": root_id,
        "from": _RUNNER_AGENT,
        "to": to,
        "subject": subject,
        "body": body,
    }
    async with make_async_client(DEFAULT_AGENT_BUS_URL, timeout=_TIMEOUT_S) as client:
        resp = await client.post("/threads/send", json=payload, headers=_auth_headers())
        resp.raise_for_status()
        return dict(resp.json())


async def post_root_checkpoint(
    root_id: str,
    *,
    subject: str,
    body: str,
    to: str = "charter-runner",
) -> dict[str, Any]:
    """Post a CHECKPOINT turn on the charter root (clears in-flight after WIP)."""
    return await post_root_turn(
        root_id, subject=subject, body=body, to=to
    )


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


async def close_root_thread(root_id: str, *, summary: str) -> dict[str, Any]:
    """Close an enrolled charter **root** thread (not a worker window).

    Uses the same PATCH ``/threads/{id}/close`` shape as ``close_worker_thread``
    (``summary``, ``mark_all_read=True``). Side effect: mutates bus thread
    status to closed so the root leaves the active enrolled set after unenroll.
    """
    payload = {
        "summary": summary,
        "mark_all_read": True,
    }
    async with make_async_client(DEFAULT_AGENT_BUS_URL, timeout=_TIMEOUT_S) as client:
        resp = await client.patch(
            f"/threads/{root_id}/close",
            json=payload,
            headers=_auth_headers(),
        )
        resp.raise_for_status()
        return dict(resp.json())


async def unenroll_root(root_id: str) -> dict[str, Any]:
    """Strip ``charter-runner`` from root tags via read-modify-write PATCH.

    Fetches the thread, removes ``ENROLLMENT_TAG`` from the tags list, then
    replaces tags with the PATCH update. Idempotent when the tag is already
    absent. Returns ``{tags, unenrolled}`` where ``unenrolled`` is True iff
    the enrollment tag is absent after the PATCH (do not use ``bool(dict)``).
    """
    detail = await fetch_thread(root_id)
    tags = list(detail.get("tags") or [])
    if ENROLLMENT_TAG in tags:
        tags = [t for t in tags if t != ENROLLMENT_TAG]
        async with make_async_client(
            DEFAULT_AGENT_BUS_URL, timeout=_TIMEOUT_S
        ) as client:
            resp = await client.patch(
                f"/threads/{root_id}",
                json={"tags": tags},
                headers=_auth_headers(),
            )
            resp.raise_for_status()
            detail = dict(resp.json())
            # Some bus shapes nest the thread under a key; normalize tags.
            if "tags" not in detail and isinstance(detail.get("thread"), dict):
                detail = dict(detail["thread"])
            tags = list(detail.get("tags") or tags)
    unenrolled = ENROLLMENT_TAG not in tags
    return {"tags": tags, "unenrolled": unenrolled}


async def fetch_thread(thread_id: str) -> dict[str, Any]:
    """Fetch one thread detail (status, summary, …)."""
    async with make_async_client(DEFAULT_AGENT_BUS_URL, timeout=_TIMEOUT_S) as client:
        resp = await client.get(
            f"/threads/{thread_id}",
            headers=_auth_headers(),
        )
        resp.raise_for_status()
        return dict(resp.json())


async def update_thread_summary(thread_id: str, summary: str) -> dict[str, Any]:
    """PATCH standing so-what title onto ``ThreadDetail.summary``."""
    payload = {"summary": summary}
    async with make_async_client(DEFAULT_AGENT_BUS_URL, timeout=_TIMEOUT_S) as client:
        resp = await client.patch(
            f"/threads/{thread_id}",
            json=payload,
            headers=_auth_headers(),
        )
        resp.raise_for_status()
        return dict(resp.json())


def _thread_summary(detail: dict[str, Any]) -> str:
    raw = str(detail.get("summary") or "")
    if not raw and isinstance(detail.get("thread"), dict):
        raw = str((detail.get("thread") or {}).get("summary") or "")
    return raw.strip()


async def ensure_root_so_what(root_id: str) -> str | None:
    """If enrolled root has empty summary, set a humanized so-what from slug.

    Seats should pass an explicit ULG so-what at mint; this is a fail-soft fill
    so pager/close never see a blank title.
    """
    from pager_notify.so_what import clip

    detail = await fetch_thread(root_id)
    prior = _thread_summary(detail)
    if prior:
        return prior
    slug = str(detail.get("slug") or "")
    if not slug and isinstance(detail.get("thread"), dict):
        slug = str((detail.get("thread") or {}).get("slug") or "")
    if not slug:
        return None
    human = slug.replace("-", " ").replace("_", " ").strip()
    summary = clip(f"ULG: {human}", 120)
    await update_thread_summary(root_id, summary)
    return summary


async def find_thread_id_by_slug(slug: str) -> str | None:
    """Return active thread id for an exact slug match, if any."""
    async with make_async_client(DEFAULT_AGENT_BUS_URL, timeout=_TIMEOUT_S) as client:
        resp = await client.get(
            "/threads",
            params=[("status", "active"), ("query", slug)],
            headers=_auth_headers(),
        )
        resp.raise_for_status()
        for thread in resp.json().get("threads") or []:
            if str(thread.get("slug") or "") == slug:
                return str(thread.get("id") or "") or None
    return None


async def create_thread(
    *,
    slug: str,
    summary: str = "",
    tags: list[str] | None = None,
    enroll_charter_runner: bool = False,
) -> str:
    """Create a standing bus thread without a turn; return its id.

    ``enroll_charter_runner`` is the dual-key the bus requires to newly add the
    reserved ``charter-runner`` tag; without it the write is denied with 422
    (``agent_bus_store.enrollment_guard``).
    """
    payload: dict[str, Any] = {"slug": slug}
    if summary:
        payload["summary"] = summary
    if tags:
        payload["tags"] = tags
    if enroll_charter_runner:
        payload["enroll_charter_runner"] = True
    async with make_async_client(DEFAULT_AGENT_BUS_URL, timeout=_TIMEOUT_S) as client:
        resp = await client.post("/threads", json=payload, headers=_auth_headers())
        resp.raise_for_status()
        return str(resp.json()["id"])


def closeout_turn_from_turns(turns: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Latest machine-closeout turn (JSON body with ``status``), if present."""
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
            return turn
    return None


def closeout_status_from_turns(turns: list[dict[str, Any]]) -> str | None:
    """Latest machine-closeout ``status`` from worker turns, if present.

    Cursor-sdk / dispatch closeouts post a JSON body with a ``status`` field
    (``complete`` | ``partial`` | ``failed`` | ``timeout``). Newest turn wins.
    """
    turn = closeout_turn_from_turns(turns)
    if turn is None:
        return None
    try:
        data = json.loads(str(turn.get("body") or "").strip())
    except (ValueError, TypeError):
        return None
    if isinstance(data, dict) and "status" in data:
        return str(data["status"]).strip().lower()
    return None


def closeout_posted_at_from_turns(turns: list[dict[str, Any]]) -> datetime | None:
    """Timestamp of the latest machine-closeout turn, if parseable."""
    turn = closeout_turn_from_turns(turns)
    if turn is None:
        return None
    raw = turn.get("created_at") or turn.get("posted_at")
    if not raw:
        return None
    try:
        text = str(raw).replace("Z", "+00:00")
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            return dt.replace(tzinfo=UTC)
        return dt
    except (TypeError, ValueError):
        return None


async def worker_failure_reason(worker_thread: str) -> str | None:
    """Return a failure reason if the worker closeout/thread is terminal-failed.

    - Closeout body ``status ∈ {failed, timeout}`` → that status.
    - Thread ``status == closed`` with no successful closeout → ``worker_closed``.
    - Otherwise ``None`` (still running, or success-shaped — see self_heal for
      ``checkpoint_missing`` when complete/partial lacks a root CHECKPOINT).
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
