"""Thread listing and creation dispatchers."""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlencode

from mcp_events import record

from ._shared import _structured_relay_error, relay

logger = logging.getLogger(__name__)

_DEFAULT_THREAD_LIMIT = 50


def _threads_impl(
    *,
    status: str,
    tags: list[str] | None = None,
    lifecycle_state: str | None = None,
    last: int | None = None,
    has_unread: bool | None = None,
    query: str | None = None,
    to: str | None = None,
) -> dict[str, Any]:
    params: list[tuple[str, str]] = []
    if status != "all":
        params.append(("status", status))
    tag_list = [t.strip() for t in (tags or []) if t and t.strip()]
    for tag in tag_list:
        params.append(("tags", tag))
    if lifecycle_state:
        params.append(("lifecycle_state", lifecycle_state))
    limit_applied = last if last is not None else _DEFAULT_THREAD_LIMIT
    params.append(("limit", str(limit_applied)))
    if has_unread is not None:
        params.append(("has_unread", "true" if has_unread else "false"))
    if query:
        params.append(("query", query))
    if to:
        params.append(("to", to))
    qs = urlencode(params)
    path = f"/threads?{qs}" if qs else "/threads"
    result = relay("agent-bus", "GET", path)

    if "error" in result:
        structured = _structured_relay_error(result, op="threads")
        if structured is not None:
            return structured
        return {"error": f"agent-bus error: {result['error']}"}

    threads: list[Any] = (
        result if isinstance(result, list) else result.get("threads", [])
    )
    count = len(threads)
    truncated = count >= limit_applied
    logger.info(
        "agent_bus threads: status=%s lifecycle=%s tags=%s -> %d threads",
        status,
        lifecycle_state or "-",
        ",".join(tag_list) or "-",
        count,
    )
    record(
        "mcp.agentbus.threads.listed",
        status=status,
        tag_count=len(tag_list),
        count=count,
        limit_applied=limit_applied,
        truncated=truncated,
    )
    if isinstance(result, dict):
        enriched = dict(result)
        enriched["limit_applied"] = limit_applied
        enriched["truncated"] = truncated
        return enriched
    return {
        "threads": threads,
        "limit_applied": limit_applied,
        "truncated": truncated,
    }


def _create_thread_impl(
    *,
    slug: str,
    summary: str | None = None,
    tags: list[str] | None = None,
    lifecycle_state: str | None = None,
    thread_id: str | None = None,
    enroll_charter_runner: bool = False,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    """Create a thread without a turn via POST /threads.

    ``idempotency_key`` makes the create retry-safe: the store answers a
    repeated key with the thread it already made (``idempotent_replay``). On a
    relay timeout the created id may be hidden from the caller, so this path
    re-posts once with the same key (safe: replay, not a sibling); without a
    key it lists exact-slug matches as ``possible_created_threads`` instead.
    """
    payload: dict[str, Any] = {"slug": slug}
    if summary is not None:
        payload["summary"] = summary
    if tags:
        payload["tags"] = tags
    if lifecycle_state is not None:
        payload["lifecycle_state"] = lifecycle_state
    if thread_id is not None:
        payload["id"] = thread_id
    if enroll_charter_runner:
        payload["enroll_charter_runner"] = True
    if idempotency_key is not None:
        payload["idempotency_key"] = idempotency_key
    result = relay("agent-bus", "POST", "/threads", body=payload)
    recovered = False
    if _is_relay_timeout(result):
        result, recovered = _recover_create_after_timeout(
            result, payload=payload, slug=slug, idempotency_key=idempotency_key
        )
    if isinstance(result, dict) and "error" in result:
        structured = _structured_relay_error(result, op="create_thread")
        if structured is not None:
            return structured
        out = {"error": f"agent-bus error creating thread: {result['error']}"}
        if "possible_created_threads" in result:
            out["possible_created_threads"] = result["possible_created_threads"]
            out["fix_hint"] = result.get("fix_hint", "")
        return out
    created_id = result.get("id", "") if isinstance(result, dict) else ""
    if recovered and isinstance(result, dict):
        result["recovered_after_timeout"] = True
    logger.info(
        "agent_bus create_thread: thread=%s slug=%s replay=%s",
        created_id,
        slug,
        bool(isinstance(result, dict) and result.get("idempotent_replay")),
    )
    record(
        "mcp.agentbus.thread.created",
        thread=created_id,
        slug=slug,
        via="create_thread",
        idempotent_replay=bool(
            isinstance(result, dict) and result.get("idempotent_replay")
        ),
    )
    return result


def _is_relay_timeout(result: Any) -> bool:
    return (
        isinstance(result, dict)
        and "error" in result
        and "status_code" not in result
        and "timed out" in str(result.get("error", ""))
    )


def _recover_create_after_timeout(
    timeout_result: dict[str, Any],
    *,
    payload: dict[str, Any],
    slug: str,
    idempotency_key: str | None,
) -> tuple[dict[str, Any], bool]:
    """Surface a create that committed behind a client timeout (a:36915).

    Keyed: one re-POST — the store replays the existing thread or, if the
    first insert never landed, creates it now. Keyless: exact-slug listing
    only; re-posting would mint the sibling the friction describes.
    """
    if idempotency_key is not None:
        retry = relay("agent-bus", "POST", "/threads", body=payload)
        if isinstance(retry, dict) and "error" not in retry:
            return retry, True
        return timeout_result, False
    listing = relay(
        "agent-bus", "GET", f"/threads?{urlencode({'query': slug, 'limit': '5'})}"
    )
    rows = listing.get("threads", []) if isinstance(listing, dict) else []
    matches = [
        {"id": r.get("id"), "created_at": r.get("created_at")}
        for r in rows
        if isinstance(r, dict) and r.get("slug") == slug
    ]
    out = dict(timeout_result)
    out["possible_created_threads"] = matches
    out["fix_hint"] = (
        "The create may have committed before the timeout. Pass idempotency_key "
        "on create_thread so a retry returns the existing thread instead of a sibling."
    )
    return out, False


def _threads_dispatch(
    *,
    status: str = "active",
    tags: list[str] | None = None,
    lifecycle_state: str | None = None,
    last: int | None = None,
    has_unread: bool | None = None,
    query: str | None = None,
    to: str | None = None,
) -> dict[str, Any]:
    return _threads_impl(
        status=status,
        tags=tags,
        lifecycle_state=lifecycle_state,
        last=last,
        has_unread=has_unread,
        query=query,
        to=to,
    )


def _enrich_with_cursor_auto_job(
    detail: dict[str, Any], *, thread: str
) -> dict[str, Any]:
    """Return the thread unchanged.

    The Auto job-state enrich left with the deleted worker client. ``thread``
    is accepted so callers keep the same signature.
    """
    del thread
    return detail


def _thread_get_impl(
    *, thread: str, include_resume: bool = False, to: str | None = None
) -> dict[str, Any]:
    """Fetch one thread by id — relay GET /threads/{thread} → ThreadDetail."""
    if isinstance(thread, int):
        thread = str(thread)
    if not thread:
        return {"error": "thread_get requires: thread (str)"}
    params: dict[str, str] = {
        "include_resume": "true" if include_resume else "false"
    }
    if to:
        params["to"] = to
    qs = urlencode(params)
    result = relay("agent-bus", "GET", f"/threads/{thread}?{qs}")
    if not isinstance(result, dict):
        return {"error": f"agent-bus error: unexpected response for thread {thread!r}"}
    if "error" in result:
        detail = result.get("detail")
        if isinstance(detail, str) and "not found" in detail.lower():
            return {
                "error": detail,
                "reason": "thread_not_found",
                "thread": thread,
            }
        structured = _structured_relay_error(result, op="thread_get")
        if structured is not None:
            return structured
        return {"error": f"agent-bus error: {result['error']}"}
    detail = _enrich_with_cursor_auto_job(result, thread=thread)
    if isinstance(detail, dict) and detail.get("cse_chat_url") is not None:
        detail["cse_chat_url_basis"] = "last_associated"
        detail["cse_current_probe"] = (
            f"cse_session(op=resolve_attended, parent_thread={thread})"
        )
    return detail


def _thread_get_dispatch(
    *,
    thread: str | int = "",
    include_resume: bool = False,
    to: str | None = None,
) -> dict[str, Any]:
    if isinstance(thread, int):
        thread = str(thread)
    return _thread_get_impl(
        thread=thread, include_resume=bool(include_resume), to=to
    )


def _job_state_dispatch(
    *,
    thread: str | int = "",
    thread_id: str | int = "",
    job_id: str = "",
    include_terminal: bool = False,
) -> dict[str, Any]:
    """Job-state observer. The Auto worker route is gone; use team_dispatch."""
    lane = str(thread or thread_id or "")
    if isinstance(thread, int) and not lane:
        lane = str(thread)
    if not lane and not job_id:
        return {
            "error": "job_state requires: thread/thread_id and/or job_id",
            "reason": "missing_key",
        }
    return {
        "found": False,
        "error": "auto_job_state_removed",
        "reason": "use team_dispatch",
        "thread": lane or None,
        "job_id": job_id or None,
        "include_terminal": bool(include_terminal),
    }


def _create_thread_dispatch(
    *,
    slug: str = "",
    summary: str | None = None,
    tags: list[str] | None = None,
    lifecycle_state: str | None = None,
    thread_id: str | None = None,
    enroll_charter_runner: bool = False,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    if not slug:
        return {"error": "create_thread requires: slug"}
    return _create_thread_impl(
        slug=slug,
        summary=summary,
        tags=tags,
        lifecycle_state=lifecycle_state,
        thread_id=thread_id,
        enroll_charter_runner=enroll_charter_runner,
        idempotency_key=idempotency_key,
    )


def _tape_dispatch(
    *,
    thread: str | int = "",
    thread_id: str | int = "",
    budget_bytes: int | None = None,
    harvest: bool | None = None,
    max_seals: int | None = None,
    scope: str | None = None,
    transcript_id: str | None = None,
    prior_cells: int | None = None,
    include_extras: bool | None = None,
    tools: str | None = None,
    channel: str | None = None,
) -> dict[str, Any]:
    """Relay GET /threads/{thread}/tape — continuity tape render."""
    lane = str(thread or thread_id or "")
    if not lane:
        return {"error": "tape requires: thread", "reason": "missing_arg"}
    params: list[str] = []
    if budget_bytes is not None:
        params.append(f"budget_bytes={int(budget_bytes)}")
    if harvest:
        params.append("harvest=true")
    if max_seals is not None:
        params.append(f"max_seals={int(max_seals)}")
    if scope:
        params.append(f"scope={scope}")
    if transcript_id:
        params.append(f"transcript_id={transcript_id}")
    if prior_cells is not None:
        params.append(f"prior_cells={int(prior_cells)}")
    if include_extras:
        params.append("include_extras=true")
    if tools:
        params.append(f"tools={tools}")
    if channel:
        params.append(f"channel={channel}")
    query = f"?{'&'.join(params)}" if params else ""
    result = relay("agent-bus", "GET", f"/threads/{lane}/tape{query}")
    if isinstance(result, dict) and "error" in result:
        structured = _structured_relay_error(result, op="tape")
        if structured is not None:
            return structured
        return {"error": f"agent-bus error: {result['error']}"}
    record("mcp.agentbus.tape.rendered", thread=lane)
    return result if isinstance(result, dict) else {"tape": result}
