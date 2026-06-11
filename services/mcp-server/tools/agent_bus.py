"""Agent-bus tools — dispatch-style MCP interface to the Agent Bus service.

Exposes a single ``agent_bus(tool=..., arguments=...)`` tool that routes to
the Agent Bus REST API over UDS. Uses the same dispatch calling convention
as the primary dispatch() tool.

All HTTP I/O delegates to ``_relay()`` from ``local_api.py``.
"""

from __future__ import annotations

import inspect
import json
import logging
import os
from typing import TYPE_CHECKING, Any
from urllib.parse import urlencode

from mcp_events import record
from mcp_toolprogress import toolprogress_begin, toolprogress_end

from ._agent_bus_post_guard import (
    reconcile_post_arguments,
    structured_route_guard,
)
from ._local_relay import relay as _relay

if TYPE_CHECKING:
    from collections.abc import Callable

    from fastmcp import FastMCP

logger = logging.getLogger(__name__)
_FETCH_CONTEXT_CAP = max(1, int(os.getenv("MCP_AGENT_BUS_CONTEXT_CAP", "50")))
_VALID_TURN_STATUSES = ("open", "resolved", "superseded", "waiting")

# Common wrong keys → canonical accepted key. Surfaced as a "did you mean"
# hint on the unknown-argument gate so callers do not have to discover the
# rename by trial (friction 16615: thread_id→thread, agent→from_agent).
_ARG_ALIASES: dict[str, str] = {
    "thread_id": "thread",
    "threadid": "thread",
    "agent": "from_agent",
    "from": "from_agent",
    "author": "from_agent",
    "message": "body",
    "msg": "body",
    "text": "body",
    "content": "body",
    "turn": "turn_number",
    "title": "subject",
}


def _relay_detail(result: dict[str, Any]) -> Any:
    """Extract FastAPI ``detail`` from a relay error envelope."""
    detail = result.get("detail")
    if detail is not None:
        return detail
    body = result.get("body")
    if isinstance(body, str):
        try:
            parsed = json.loads(body)
        except json.JSONDecodeError:
            return None
        if isinstance(parsed, dict):
            return parsed.get("detail")
    return None


def _format_agent_bus_error(result: dict[str, Any], *, op: str) -> str:
    """Turn relay failures into agent-actionable messages."""
    if result.get("status_code") == 422:
        detail = _relay_detail(result)
        if isinstance(detail, list):
            for err in detail:
                if not isinstance(err, dict):
                    continue
                loc = err.get("loc") or ()
                if any(part == "status" for part in loc):
                    allowed = ", ".join(_VALID_TURN_STATUSES)
                    return (
                        f"{op}: invalid status value — turn status must be one of: "
                        f"{allowed}. Thread status (active|blocked|waiting|closed) "
                        "belongs on update_thread, not reply/post."
                    )
    return f"agent-bus error: {result.get('error', 'unknown error')}"


def _unknown_arg_error(
    *, tool: str, unknown: list[str], accepted: set[str]
) -> dict[str, Any]:
    """Build the unsupported-argument rejection, with canonical-alias hints.

    Maps common wrong keys (``thread_id``, ``agent`` …) to the accepted key so
    callers fix the rename in one shot rather than by trial (friction 16615).
    """
    ordered = sorted(unknown)
    hints = [
        f"{k!r} → {_ARG_ALIASES[k]!r}"
        for k in ordered
        if _ARG_ALIASES.get(k) in accepted
    ]
    hint_suffix = f" Did you mean: {', '.join(hints)}." if hints else ""
    return {
        "error": (
            f"{tool}: unsupported argument(s): {', '.join(ordered)}. "
            f"Accepted: {sorted(accepted)}.{hint_suffix}"
        )
    }


def _unread_turns_remediation(detail: dict[str, Any]) -> str:
    """Build the actionable mark-read remediation for a 409 unread_turns_exist.

    The REST 409 body carries ``unread_turns`` (list of {thread, turn_number})
    but no remediation verb — callers previously had to discover the
    get+mark_read flow by trial (friction 16615). Name the exact op and turns.
    """
    unread = detail.get("unread_turns") or []
    pairs = [
        (str(t.get("thread")), t.get("turn_number"))
        for t in unread
        if isinstance(t, dict) and t.get("turn_number") is not None
    ]
    if not pairs:
        return (
            "Remediation: mark the turns addressed to you read first — "
            "fetch_unread(to=<you>, mark_read=true), or mark_read(thread, "
            "turn_number) per turn — then retry."
        )
    calls = "; ".join(
        f'mark_read(thread="{thread}", turn_number={n})' for thread, n in pairs
    )
    return (
        "Remediation: mark each blocking turn read first, then retry — "
        f"{calls}. (Or fetch_unread(to=<you>, mark_read=true) to clear all.)"
    )


def _structured_relay_error(
    result: dict[str, Any], *, op: str
) -> dict[str, Any] | None:
    """Preserve relay ``status_code`` and structured ``detail`` for MCP callers."""
    status_code = result.get("status_code")
    detail = _relay_detail(result)
    if status_code is None and detail is None:
        return None
    reason = detail.get("error") if isinstance(detail, dict) else None
    base_error = result.get("error", "request failed")
    message = f"{op}: {base_error}"
    if reason:
        message = f"{message} ({reason})"
    envelope: dict[str, Any] = {
        "error": message,
        "status_code": status_code,
        "reason": reason,
        "detail": detail,
    }
    if reason == "unread_turns_exist" and isinstance(detail, dict):
        remediation = _unread_turns_remediation(detail)
        envelope["error"] = f"{message}. {remediation}"
        envelope["remediation"] = remediation
    return envelope


def _structured_body_too_large(
    result: dict[str, Any], *, op: str
) -> dict[str, Any] | None:
    """Re-shape a relay 413 detail into the legacy structured error envelope.

    REST returns ``{"detail": {"reason": "body_too_large", "limit_chars": ...,
    "body_chars": ..., "suggestion": ..., "message": ...}}`` on 413; the relay
    surfaces ``detail`` alongside ``error``. Agents previously got
    ``{error, reason, limit_chars, body_chars, suggestion}`` from the MCP
    preflight — preserve that shape so existing callers keep their
    discriminator fields.
    """
    detail = result.get("detail")
    if not (isinstance(detail, dict) and detail.get("reason") == "body_too_large"):
        return None
    limit = detail.get("limit_chars")
    body_chars = detail.get("body_chars")
    return {
        "error": (
            f"{op}: turn body exceeds limit "
            f"({body_chars:,} chars, limit {limit:,}). "
            "Agent-bus convention: short briefing + Cortex sidecar markdown "
            "reference. Write long content with fs(sandbox='cortex', op='write') "
            "to notes/system/threads/<thread>-<subject>.md "
            "and reference it in a brief body. If inline long-form delivery is "
            "required for this recipient, retry with allow_long_body=true."
        ),
        "reason": "body_too_large",
        "limit_chars": limit,
        "body_chars": body_chars,
        "suggestion": detail.get("suggestion", "sidecar_markdown_or_allow_long_body"),
    }


# ── Impl helpers ────────────────────────────────────────────────────


def _post_impl(
    *,
    slug: str,
    to: str,
    subject: str,
    body: str,
    from_agent: str,
    summary: str | None,
    attachments: list[dict[str, Any]] | None = None,
    tags: list[str] | None = None,
    allow_long_body: bool = False,
) -> dict[str, Any]:
    """Atomic thread+turn creation via POST /threads/with-turn."""
    payload: dict[str, Any] = {
        "slug": slug,
        "from": from_agent,
        "to": to,
        "subject": subject,
        "body": body,
        "status": "open",
        "after_turn": 0,
    }
    if summary is not None:
        payload["summary"] = summary
    if attachments:
        payload["attachments"] = attachments
    if tags:
        payload["tags"] = tags
    if allow_long_body:
        payload["allow_long_body"] = True

    result = _relay("agent-bus", "POST", "/threads/with-turn", body=payload)
    if "error" in result:
        record("mcp.agentbus.post.failed", slug=slug, to=to, error=str(result["error"]))
        structured = _structured_body_too_large(result, op="post")
        if structured is not None:
            return structured
        guard = structured_route_guard(result)
        if guard is not None:
            return guard
        structured = _structured_relay_error(result, op="post")
        if structured is not None:
            return structured
        return {"error": _format_agent_bus_error(result, op="post")}

    thread_data = result.get("thread", {})
    turn_data = result.get("turn", {})
    thread_id = thread_data.get("id", "")
    turn_number = turn_data.get("turn_number", 1)

    logger.info("agent_bus post: thread=%s slug=%s to=%s", thread_id, slug, to)
    record(
        "mcp.agentbus.thread.created",
        thread=thread_id,
        slug=slug,
        to=to,
        turn_number=turn_number,
    )
    if tags:
        record(
            "mcp.agentbus.thread.tags.updated",
            thread=thread_id,
            tag_count=len(tags),
            agent=from_agent,
            op="post",
        )
    return result


def _reply_impl(
    *,
    thread: str,
    to: str,
    subject: str,
    body: str,
    after_turn: int,
    from_agent: str,
    status: str,
    mark_read: bool,
    close: bool,
    attachments: list[dict[str, Any]] | None = None,
    allow_long_body: bool = False,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "thread": thread,
        "from": from_agent,
        "to": to,
        "subject": subject,
        "body": body,
        "status": status,
    }
    # after_turn=0 means "skip the unread-concurrency check"; the REST contract
    # treats absent/None the same way. Forwarding 0 verbatim would be read as
    # "fail if any turn > 0 is unread" — i.e. always — defeating the broadcast-
    # thread use case (480, etc.) the zero sentinel exists for.
    if after_turn > 0:
        payload["after_turn"] = after_turn
    if attachments:
        payload["attachments"] = attachments
    if allow_long_body:
        payload["allow_long_body"] = True
    result = _relay("agent-bus", "POST", "/turns", body=payload)

    if "error" in result:
        structured = _structured_body_too_large(result, op="reply")
        if structured is not None:
            return structured
        structured = _structured_relay_error(result, op="reply")
        if structured is not None:
            return structured
        return {"error": _format_agent_bus_error(result, op="reply")}

    turn_number = result.get("turn_number") or result.get("id")
    effective_turn_number = turn_number if turn_number is not None else 1
    logger.info(
        "agent_bus reply: thread=%s to=%s turn=%s", thread, to, effective_turn_number
    )
    record(
        "mcp.agentbus.turn.posted",
        thread=thread,
        to=to,
        turn_number=effective_turn_number,
    )

    if mark_read:
        turn_id = result.get("id")
        if turn_id is not None:
            _relay("agent-bus", "PATCH", f"/turns/{turn_id}/read")
            logger.info("agent_bus reply: marked turn %s read (self-note)", turn_number)

    if close:
        close_result = _relay("agent-bus", "PATCH", f"/threads/{thread}/close", body={})
        if isinstance(close_result, dict) and "error" in close_result:
            return {
                "error": (
                    f"reply posted but close failed: {close_result['error']}. "
                    f"Turn {effective_turn_number} exists; close manually."
                )
            }
        logger.info("agent_bus reply: closed thread %s after final turn", thread)
        record("mcp.agentbus.thread.closed", thread=thread, via="reply")
        result["closed"] = True

    return result


def _fetch_impl(
    *,
    to: str | None,
    thread: str | None,
    last: int | None,
    unread: bool,
    mark_read: bool,
    compact: bool,
) -> dict[str, Any]:
    if to is None and thread is None:
        return {"error": "fetch requires at least one of: to, thread"}

    params: dict[str, Any] = {}
    if thread is not None:
        params["thread"] = thread
    if to is not None:
        params["to"] = to
    if unread:
        params["unread"] = "true"
    if compact:
        params["compact"] = "true"
    if last is not None:
        params["last"] = last
    if mark_read:
        params["mark_read"] = "true"

    qs = urlencode(params)
    result = _relay("agent-bus", "GET", f"/turns?{qs}")

    if "error" in result:
        return {"error": f"agent-bus error: {result['error']}"}

    turns: list[Any] = result if isinstance(result, list) else result.get("turns", [])
    count = len(turns)
    logger.info(
        "agent_bus fetch: to=%s thread=%s mark_read=%s -> %d turns",
        to,
        thread,
        mark_read,
        count,
    )
    record(
        "mcp.agentbus.turns.fetched",
        to=to or "",
        thread=thread or "",
        count=count,
        mark_read=mark_read,
    )
    return result


def _fetch_unread_toc_impl(
    *, to: str, mark_read: bool, limit: int | None = None
) -> dict[str, Any]:
    """Recipient-scoped unread inbox digest via GET /turns/unread-toc.

    Bounded by thread count, so the post-boot catch-up read stays under the MCP
    inline response guard regardless of unread turn volume (friction 16835).
    Thin relay — the per-thread aggregation lives in the agent-bus store.
    """
    params: dict[str, Any] = {"to": to}
    if mark_read:
        params["mark_read"] = "true"
    if limit is not None:
        params["limit"] = limit
    qs = urlencode(params)
    result = _relay("agent-bus", "GET", f"/turns/unread-toc?{qs}")

    if isinstance(result, dict) and "error" in result:
        return {"error": f"agent-bus error: {result['error']}"}

    thread_count = len(result.get("threads", [])) if isinstance(result, dict) else 0
    logger.info(
        "agent_bus fetch_unread (toc): to=%s mark_read=%s -> %d threads",
        to,
        mark_read,
        thread_count,
    )
    record(
        "mcp.agentbus.unread_toc.fetched",
        to=to,
        thread_count=thread_count,
        mark_read=mark_read,
    )
    return result


def _get_impl(*, thread: str, turn_number: int) -> dict[str, Any]:
    """Direct single-turn lookup via GET /turns/by-number."""
    qs = urlencode({"thread": thread, "turn_number": turn_number})
    result = _relay("agent-bus", "GET", f"/turns/by-number?{qs}")
    if isinstance(result, dict) and "error" in result:
        return {"error": f"agent-bus error: {result['error']}"}
    record("mcp.agentbus.turn.detail.fetched", thread=thread, turn_number=turn_number)
    return {"turn": result}  # Return type should be dict[str, dict[str, Any]]


def _resolve_turn_id(
    *, thread: str, turn_number: int
) -> tuple[int | None, dict[str, Any] | None]:
    """Resolve (thread, turn_number) to turn id via direct lookup."""
    qs = urlencode({"thread": thread, "turn_number": turn_number})
    result = _relay("agent-bus", "GET", f"/turns/by-number?{qs}")
    if isinstance(result, dict) and "error" in result:
        return None, {"error": f"agent-bus error: {result['error']}"}
    if isinstance(result, dict) and "id" in result:
        return int(result["id"]), None
    return None, {"error": f"Turn {turn_number} not found in thread {thread}"}


def _threads_impl(
    *,
    status: str,
    tags: list[str] | None = None,
    lifecycle_state: str | None = None,
    last: int | None = None,
    limit: int | None = None,
    has_unread: bool | None = None,
) -> dict[str, Any]:
    params: list[tuple[str, str]] = []
    if status != "all":
        params.append(("status", status))
    tag_list = [t.strip() for t in (tags or []) if t and t.strip()]
    for tag in tag_list:
        params.append(("tags", tag))
    if lifecycle_state:
        params.append(("lifecycle_state", lifecycle_state))
    # Support schema-declared params (last is ritual/boot alias for limit; backend uses limit)
    effective_limit = limit if limit is not None else last
    if effective_limit is not None:
        params.append(("limit", str(effective_limit)))
    if has_unread is not None:
        params.append(("has_unread", "true" if has_unread else "false"))
    qs = urlencode(params)
    path = f"/threads?{qs}" if qs else "/threads"
    result = _relay("agent-bus", "GET", path)

    if "error" in result:
        return {"error": f"agent-bus error: {result['error']}"}

    threads: list[Any] = (
        result if isinstance(result, list) else result.get("threads", [])
    )
    count = len(threads)
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
    )
    return result


def _create_thread_impl(
    *,
    slug: str,
    summary: str | None = None,
    tags: list[str] | None = None,
    lifecycle_state: str | None = None,
    thread_id: str | None = None,
) -> dict[str, Any]:
    """Create a thread without a turn via POST /threads."""
    payload: dict[str, Any] = {"slug": slug}
    if summary is not None:
        payload["summary"] = summary
    if tags:
        payload["tags"] = tags
    if lifecycle_state is not None:
        payload["lifecycle_state"] = lifecycle_state
    if thread_id is not None:
        payload["id"] = thread_id
    result = _relay("agent-bus", "POST", "/threads", body=payload)
    if isinstance(result, dict) and "error" in result:
        return {"error": f"agent-bus error creating thread: {result['error']}"}
    created_id = result.get("id", "") if isinstance(result, dict) else ""
    logger.info("agent_bus create_thread: thread=%s slug=%s", created_id, slug)
    record(
        "mcp.agentbus.thread.created",
        thread=created_id,
        slug=slug,
        via="create_thread",
    )
    return result


def _close_impl(
    *,
    thread: str,
    summary: str | None,
    mark_all_read: bool,
) -> dict[str, Any]:
    """Atomic close via PATCH /threads/{id}/close."""
    payload: dict[str, Any] = {"mark_all_read": mark_all_read}
    if summary is not None:
        payload["summary"] = summary
    result = _relay("agent-bus", "PATCH", f"/threads/{thread}/close", body=payload)
    if "error" in result:
        return {"error": f"agent-bus error: {result['error']}"}
    logger.info("agent_bus close: thread=%s", thread)
    record("mcp.agentbus.thread.closed", thread=thread)
    return result


def _update_thread_impl(
    *,
    thread: str,
    status: str | None,
    summary: str | None,
    tags: list[str] | None,
    from_agent: str,
) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    if status is not None:
        payload["status"] = status
    if summary is not None:
        payload["summary"] = summary
    if tags is not None:
        # [] = clear, [...] = replace. None = omit so server leaves unchanged.
        payload["tags"] = tags
    if not payload:
        return {
            "error": "update_thread requires at least one of: status, summary, tags"
        }
    result = _relay("agent-bus", "PATCH", f"/threads/{thread}", body=payload)
    if "error" in result:
        return {"error": f"agent-bus error: {result['error']}"}
    logger.info("agent_bus update_thread: thread=%s status=%s", thread, status)
    record("mcp.agentbus.thread.updated", thread=thread, status=status or "")
    if tags is not None:
        record(
            "mcp.agentbus.thread.tags.updated",
            thread=thread,
            tag_count=len(tags),
            agent=from_agent,
            op="update_thread",
        )
    return result


def _update_impl(
    *,
    thread: str,
    turn_number: int,
    body: str | None,
    append: bool | str | None,
    subject: str | None,
) -> dict[str, Any]:
    turn_id, resolve_error = _resolve_turn_id(thread=thread, turn_number=turn_number)
    if resolve_error is not None:
        return resolve_error

    patch_body: dict[str, str | None] = {}
    if isinstance(append, str):
        patch_body["append"] = append
    elif append:
        if body is None:
            return {"error": "update with append=true requires body"}
        patch_body["append"] = body
    elif body is not None:
        patch_body["body"] = body
    if subject is not None:
        patch_body["subject"] = subject

    patch_result = _relay("agent-bus", "PATCH", f"/turns/{turn_id}", body=patch_body)
    if isinstance(patch_result, dict) and "error" in patch_result:
        return {"error": f"agent-bus error: {patch_result['error']}"}

    logger.info(
        "agent_bus update: thread=%s turn=%d id=%d", thread, turn_number, turn_id
    )
    record(
        "mcp.agentbus.turn.updated",
        thread=thread,
        turn_number=turn_number,
        has_append=bool(append),
    )
    return patch_result


def _delete_thread_impl(*, thread: str, force: bool) -> dict[str, Any]:
    params = {"force": "true"} if force else {}
    qs = urlencode(params)
    path = f"/threads/{thread}?{qs}" if qs else f"/threads/{thread}"
    result = _relay("agent-bus", "DELETE", path)
    if isinstance(result, dict) and "error" in result:
        return {"error": f"agent-bus error: {result['error']}"}
    deleted_turns = result.get("deleted_turns", 0) if isinstance(result, dict) else 0
    logger.info(
        "agent_bus delete_thread: thread=%s force=%s deleted_turns=%d",
        thread,
        force,
        deleted_turns,
    )
    record(
        "mcp.agentbus.thread.deleted",
        thread=thread,
        force=force,
        deleted_turns=deleted_turns,
    )
    return result


def _delete_turn_impl(*, thread: str, turn_number: int, force: bool) -> dict[str, Any]:
    turn_id, resolve_error = _resolve_turn_id(thread=thread, turn_number=turn_number)
    if resolve_error is not None:
        return resolve_error
    force_params = urlencode({"force": "true"}) if force else ""
    path = f"/turns/{turn_id}?{force_params}" if force_params else f"/turns/{turn_id}"
    delete_result = _relay("agent-bus", "DELETE", path)
    if isinstance(delete_result, dict) and "error" in delete_result:
        return {"error": f"agent-bus error: {delete_result['error']}"}
    logger.info(
        "agent_bus delete_turn: thread=%s turn=%d id=%d force=%s",
        thread,
        turn_number,
        turn_id,
        force,
    )
    record(
        "mcp.agentbus.turn.deleted", thread=thread, turn_number=turn_number, force=force
    )
    return delete_result


# ── Dispatch wrappers (validation + defaults for JSON dispatch) ─────────────


def _fetch_unread_dispatch(
    *,
    to: str | None = None,
    thread: str | int | None = None,
    mark_read: bool = False,
    compact: bool = False,
) -> dict[str, Any]:
    """Fetch unread turns.

    Recipient scope (``to`` set, ``thread`` unset) returns a bounded per-thread
    inbox digest (UnreadThreadToc) — one row per thread with unread turns
    addressed to the seat — so the catch-up read stays under the MCP inline
    response guard regardless of unread volume (friction 16835). ``compact`` is
    moot at recipient scope; the digest never carries turn bodies. Thread scope
    (``thread`` set) returns that thread's full unread turn list (List[Turn], no
    count cap; ``compact`` controls body projection).
    """
    if isinstance(thread, int):
        thread = str(thread)
    effective_to = to if to else None
    effective_thread = thread if thread else None
    if effective_to is None and effective_thread is None:
        return {"error": "fetch_unread requires at least one of: to, thread"}
    if effective_thread is None and effective_to is not None:
        # Recipient-scoped catch-up: bounded thread digest, not an uncapped
        # turn fan-out (friction 16835).
        return _fetch_unread_toc_impl(to=effective_to, mark_read=mark_read)
    return _fetch_impl(
        to=effective_to,
        thread=effective_thread,
        last=None,
        unread=True,
        mark_read=mark_read,
        compact=compact,
    )


def _fetch_dispatch(
    *,
    to: str | None = None,
    thread: str | int | None = None,
    last: int = 10,
    unread: bool = False,
    mark_read: bool = False,
    compact: bool = False,
    all: bool = False,
) -> dict[str, Any]:
    """Dispatch wrapper for fetch — normalizes empty strings and resolves last/all/unread.

    Semantics:
    - all=True  → no limit (fetches every matching turn); overrides last
    - unread=True → no limit on the unread set; last is ignored (use fetch_unread)
    - otherwise  → last capped at MCP_AGENT_BUS_CONTEXT_CAP (default 50)
    - compact defaults False so windowed fetches project turn `body` (matching
      fetch_unread, get, and the CLI). A True default silently nulled bodies on
      thread-only/windowed fetches — a populated thread read as empty (BUG 4,
      thread 1154). Pass compact=true explicitly for metadata-only byte savings;
      the response_size_guard windows oversize payloads regardless.
    """
    if isinstance(thread, int):
        thread = str(thread)
    effective_to = to if to else None
    effective_thread = thread if thread else None
    if all:
        effective_last = None
    elif unread:
        effective_last = None
    else:
        effective_last = max(1, min(last, _FETCH_CONTEXT_CAP))
    return _fetch_impl(
        to=effective_to,
        thread=effective_thread,
        last=effective_last,
        unread=unread,
        mark_read=mark_read,
        compact=compact,
    )


def _post_dispatch(
    *,
    slug: str = "",
    to: str = "",
    subject: str = "",
    body: str = "",
    from_agent: str = "",
    summary: str | None = None,
    attachments: list[dict[str, Any]] | None = None,
    tags: list[str] | None = None,
    allow_long_body: bool = False,
) -> dict[str, Any]:
    missing: list[str] = []
    if not slug:
        missing.append("slug (str)")
    if not to:
        missing.append("to (str)")
    if not subject:
        missing.append("subject (str)")
    if not body:
        missing.append("body (str)")
    if not from_agent:
        missing.append(
            "from_agent (str, REQUIRED — no default; name the seat authoring "
            'this turn, e.g. "cursor", "claude-web", "gpt-cursor", "claude-api")'
        )
    if missing:
        return {
            "error": f"post: missing required field(s): {'; '.join(missing)}",
            "missing_fields": [f.split(" ")[0] for f in missing],
        }
    return _post_impl(
        slug=slug,
        to=to,
        subject=subject,
        body=body,
        from_agent=from_agent,
        summary=summary,
        attachments=attachments,
        tags=tags,
        allow_long_body=allow_long_body,
    )


def _reply_dispatch(
    *,
    thread: str | int = "",
    to: str = "",
    subject: str = "",
    body: str = "",
    after_turn: int = 0,
    from_agent: str = "",
    status: str = "open",
    mark_read: bool = False,
    close: bool = False,
    attachments: list[dict[str, Any]] | None = None,
    allow_long_body: bool = False,
) -> dict[str, Any]:
    # Coerce integer thread IDs — agents frequently pass bare ints from JSON.
    if isinstance(thread, int):
        thread = str(thread)

    missing: list[str] = []
    if not thread:
        missing.append('thread (str, e.g. "480")')
    if not to:
        missing.append("to (str)")
    if not subject:
        missing.append("subject (str)")
    if not body:
        missing.append("body (str)")
    # after_turn is a concurrency-check hint — required for replies into
    # interactive coordination threads where unread peer turns block posting.
    # Pass 0 to skip the check (broadcast/fan-out threads like 480 where no
    # acknowledgment is expected from the recipient set).
    if after_turn < 0:
        missing.append("after_turn (int >= 0; 0 skips the unread-concurrency check)")
    if not from_agent:
        missing.append(
            "from_agent (str, REQUIRED — no default; name the seat authoring "
            'this turn, e.g. "cursor", "claude-web", "gpt-cursor", "claude-api")'
        )
    if missing:
        return {
            "error": f"reply: missing required field(s): {'; '.join(missing)}",
            "missing_fields": [f.split(" ")[0] for f in missing],
        }
    return _reply_impl(
        thread=thread,
        to=to,
        subject=subject,
        body=body,
        after_turn=after_turn,
        from_agent=from_agent,
        status=status,
        mark_read=mark_read,
        close=close,
        attachments=attachments,
        allow_long_body=allow_long_body,
    )


def _get_dispatch(*, thread: str | int = "", turn_number: int = 0) -> dict[str, Any]:
    if isinstance(thread, int):
        thread = str(thread)
    if not thread or turn_number < 1:
        return {"error": "get requires: thread (str), turn_number (int >= 1)"}
    return _get_impl(thread=thread, turn_number=turn_number)


def _threads_dispatch(
    *,
    status: str = "active",
    tags: list[str] | None = None,
    lifecycle_state: str | None = None,
    last: int | None = None,
    limit: int | None = None,
    has_unread: bool | None = None,
) -> dict[str, Any]:
    return _threads_impl(
        status=status,
        tags=tags,
        lifecycle_state=lifecycle_state,
        last=last,
        limit=limit,
        has_unread=has_unread,
    )


def _create_thread_dispatch(
    *,
    slug: str = "",
    summary: str | None = None,
    tags: list[str] | None = None,
    lifecycle_state: str | None = None,
    thread_id: str | None = None,
) -> dict[str, Any]:
    if not slug:
        return {"error": "create_thread requires: slug"}
    return _create_thread_impl(
        slug=slug,
        summary=summary,
        tags=tags,
        lifecycle_state=lifecycle_state,
        thread_id=thread_id,
    )


def _update_dispatch(
    *,
    thread: str | int = "",
    turn_number: int = 0,
    body: str | None = None,
    append: bool | str | None = None,
    subject: str | None = None,
) -> dict[str, Any]:
    if isinstance(thread, int):
        thread = str(thread)
    if not thread or turn_number < 1:
        return {"error": "update requires: thread (str), turn_number (int >= 1)"}
    if body is None and append is None and not subject:
        return {"error": "update requires at least one of: body, append, subject"}
    if append is True and body is None:
        return {"error": "update with append=true requires body"}
    return _update_impl(
        thread=thread,
        turn_number=turn_number,
        body=body,
        append=append,
        subject=subject,
    )


def _update_thread_dispatch(
    *,
    thread: str | int = "",
    status: str | None = None,
    summary: str | None = None,
    tags: list[str] | None = None,
    from_agent: str = "cursor",
) -> dict[str, Any]:
    if isinstance(thread, int):
        thread = str(thread)
    if not thread:
        return {"error": "update_thread requires: thread (str)"}
    effective_status = status if (status and status != "open") else None
    return _update_thread_impl(
        thread=thread,
        status=effective_status,
        summary=summary,
        tags=tags,
        from_agent=from_agent,
    )


def _close_dispatch(
    *,
    thread: str | int = "",
    summary: str | None = None,
    mark_all_read: bool = True,
) -> dict[str, Any]:
    if isinstance(thread, int):
        thread = str(thread)
    if not thread:
        return {"error": "close requires: thread (str)"}
    return _close_impl(
        thread=thread,
        summary=summary,
        mark_all_read=mark_all_read,
    )


def _delete_thread_dispatch(
    *, thread: str | int = "", force: bool = False
) -> dict[str, Any]:
    if isinstance(thread, int):
        thread = str(thread)
    if not thread:
        return {"error": "delete_thread requires: thread (str)"}
    return _delete_thread_impl(thread=thread, force=force)


def _delete_turn_dispatch(
    *, thread: str | int = "", turn_number: int = 0, force: bool = False
) -> dict[str, Any]:
    if isinstance(thread, int):
        thread = str(thread)
    if not thread or turn_number < 1:
        return {"error": "delete_turn requires: thread (str), turn_number (int >= 1)"}
    return _delete_turn_impl(thread=thread, turn_number=turn_number, force=force)


def _mark_read_dispatch(
    *, thread: str | int = "", turn_number: int = 0
) -> dict[str, Any]:
    """Mark a specific turn as read. Clears it from unread counts."""
    if isinstance(thread, int):
        thread = str(thread)
    if not thread or turn_number < 1:
        return {"error": "mark_read requires: thread (str), turn_number (int >= 1)"}
    turn_id, err = _resolve_turn_id(thread=thread, turn_number=turn_number)
    if err:
        return err
    result = _relay("agent-bus", "PATCH", f"/turns/{turn_id}/read")
    if isinstance(result, dict) and "error" in result:
        return result
    logger.info("agent_bus mark_read: thread=%s turn=%d", thread, turn_number)
    record("mcp.agentbus.turn.mark.read", thread=thread, turn_number=turn_number)
    return {"status": "ok", "thread": thread, "turn_number": turn_number}


def _wait_dispatch(
    *,
    thread: str | int = "",
    after_turn: int = 1,
    wait_seconds: float = 0.0,
    completion: str = "first_reply_from",
    from_agent: str | None = None,
) -> dict[str, Any]:
    """Thin relay to agent-bus GET /threads/{id}/wait.

    Server-side block; ONE HTTP call. wait_seconds clamped <= 60 (server also
    clamps). No client poll loop — re-call this op to continue polling.
    """
    if isinstance(thread, int):
        thread = str(thread)
    if not thread:
        return {"error": 'wait requires: thread (str, e.g. "1234")'}
    if completion == "first_reply_from" and not from_agent:
        return {"error": "wait with completion=first_reply_from requires from_agent"}
    wait_clamped = max(0.0, min(wait_seconds, 60.0))
    params: list[tuple[str, str]] = [
        ("after_turn", str(after_turn)),
        ("wait", str(wait_clamped)),
        ("completion", completion),
    ]
    if from_agent:
        params.append(("from_agent", from_agent))
    qs = urlencode(params)
    record("mcp.agentbus.wait.called", thread=thread, completion=completion)
    result = _relay("agent-bus", "GET", f"/threads/{thread}/wait?{qs}")
    if isinstance(result, dict) and "error" in result:
        return {"error": f"agent-bus error: {result['error']}"}
    record(
        "mcp.agentbus.wait.completed",
        thread=thread,
        status=str(result.get("status", "")) if isinstance(result, dict) else "",
    )
    return result


AGENT_BUS_OPS: dict[str, Callable[..., Any]] = {
    "post": _post_dispatch,
    "reply": _reply_dispatch,
    "fetch": _fetch_dispatch,
    "fetch_unread": _fetch_unread_dispatch,
    "get": _get_dispatch,
    "threads": _threads_dispatch,
    "create_thread": _create_thread_dispatch,
    "close": _close_dispatch,
    "update_thread": _update_thread_dispatch,
    "update": _update_dispatch,
    "delete_thread": _delete_thread_dispatch,
    "delete_turn": _delete_turn_dispatch,
    "mark_read": _mark_read_dispatch,
    "wait": _wait_dispatch,
}


# ── Registration ────────────────────────────────────────────────────


def register_agent_bus_tools(mcp: FastMCP) -> None:
    """Register the dispatch-style agent_bus tool on the MCP server instance."""

    @mcp.tool(title="Agent Bus")
    def agent_bus(tool: str, arguments: str = "{}") -> Any:
        """Inter-agent message bus — threads, turns, read/reply coordination.

        tool: operation name (see table below)
        arguments: JSON-encoded object string (e.g. '{"thread": "111"}')

        Body convention — SIDECAR-DRIVEN (read before posting):
          Turn bodies MUST be short briefings (target < 2 KB; server enforces a
          hard char limit). Long content — reviews, specs, analysis, handoffs —
          goes in a Cortex sidecar markdown file written BEFORE the post/reply
          call. Cortex is the canonical persistence surface for bus sidecars:
            fs(sandbox="cortex", op="write",
               path="notes/system/threads/<thread>-<subject>.md",
               content="...")
          The turn body then references it concisely:
            "Review complete. Full findings: cortex:notes/system/threads/949-review.md"
          Workspace files (for example tmp/reviews packets) may be mirrors, but
          agent_bus messages should point first to the Cortex sidecar.
          Posting an oversized body returns 413. The sidecar pattern is not a
          workaround for the limit — it IS the intended usage model. Brief body
          + durable sidecar file is always preferred over an inline wall of text.
          Rare exception: pass allow_long_body=true on post/reply only when the
          recipient needs inline long-form content and a sidecar would break the
          communication contract.

        Operations:
          threads       (status?, tags?, lifecycle_state?)              — list threads; status: active|blocked|waiting|closed|all (default active); tags: AND-filter; lifecycle_state: pending|admitted|delivered|failed (exact match)
          create_thread (slug, summary?, tags?, lifecycle_state?, thread_id?) — create a thread without a turn; use lifecycle_state="pending" for lifecycle-managed threads that will be dispatched later
          fetch_unread  (to?, thread?, mark_read?, compact?)                        — recipient scope (to set, thread unset): bounded per-thread unread digest (one row per thread). thread scope: that thread's full unread turn list (no count cap; compact controls bodies). At least one of to/thread required.
          fetch         (to?, thread?, last?, unread?, compact?, mark_read?, all?)  — get turns; at least one of to/thread required; all=true fetches every turn (no limit); unread=true fetches all unread (last ignored; prefer fetch_unread); last caps windowed fetches (default 10, unread default false); compact default false (bodies projected) — pass compact=true for metadata-only
          get           (thread, turn_number)                           — get one specific turn
          post          (slug, to, subject, body, from_agent, summary?, attachments?, tags?, allow_long_body?) — start a new thread (atomic: creates thread + first turn). from_agent is REQUIRED — name the seat authoring the turn (e.g. "cursor", "claude-web", "gpt-cursor", "claude-api"); there is no default.
          reply         (thread, to, subject, body, after_turn, from_agent, status?, mark_read?, close?, attachments?, allow_long_body?) — reply to a thread; allow_long_body=true explicitly bypasses the 8k briefing limit for rare inline long-form messages; close=true posts this as the final turn and closes the thread (marks all turns read). from_agent is REQUIRED — name the seat authoring the turn; there is no default.
          update        (thread, turn_number, body?, append?, subject?) — edit or append to an existing turn
          mark_read     (thread, turn_number)                           — mark a turn as read
          wait          (thread, after_turn?, wait_seconds?, completion?, from_agent?) — server-side short-block until consult posts a bus turn after the pointer (completion=first_reply_from + canonical from_agent; alias-aware) or thread closes (completion=thread_closed); wait_seconds clamped <=60 (0=snapshot). Returns {thread_id, complete, status, push_required, suggested_next (object: consult_turn_posted + steps fetch/apply/close when complete and thread still active), qualifying_reply_turn, thread_status, ...}. first_reply_from complete means a consult bus turn exists, not findings applied. Re-call to keep polling — one HTTP call, not a client loop.
          update_thread (thread, status?, summary?, tags?, from_agent?) — patch thread metadata (tags: omit=keep, []=clear, [...]=replace)
          close         (thread, summary?, mark_all_read?)              — close a thread (atomic: marks all turns read by default)
          delete_turn   (thread, turn_number, force?)                   — delete a single turn
          delete_thread (thread, force?)                                — delete an entire thread

        Thread response fields (ThreadDetail):
          id, slug, status, summary, turn_count, unread_count, tags, created_at, updated_at
          bus_lifecycle_state: str | null — lifecycle state for dispatch-managed threads
            (pending → admitted → delivered; null = not lifecycle-managed)
          dispatch_links: list — pipeline executions linked to this thread via dispatch-admit;
            each entry has: execution_id, pipeline_id, linked_at, terminal_status, delivery_at

        Turn response fields (Turn — returned by fetch, fetch_unread, get, and as
        the created turn inside post/reply):
          id, thread, turn_number, from, to, subject, body,
          status (TurnStatus — see Status enums below), supersedes_turn,
          created_at, read_at, attachments
          NOTE: the author field serializes on the wire as `from` (the create/reply
            INPUT field is `from_agent`); the recipient field is `to`.
          `status` on a turn is the PER-TURN TurnStatus, NOT the thread's status.
            Do not infer thread state from a turn's `status` — a closed thread can
            still contain turns whose status is `open`. To check whether a thread
            is active or closed, use threads() and read the ThreadStatus `status`
            field on ThreadDetail.

        Status enums (two distinct fields — not the same field, not interchangeable;
        both happen to include `waiting`):
          TurnStatus   (per-turn workflow state, on each Turn):     open | resolved | superseded | waiting
          ThreadStatus (thread-level lifecycle, on ThreadDetail):   active | blocked | waiting | closed

        Tags (free-form strings on threads):
          Suggested `namespace:value` convention — nothing is enforced:
            project:<name>   — project scoping (e.g. project:claudeburst)
            type:<kind>      — intent (bug|feature|discussion|review|post-mortem)
            agent:<name>     — agent ownership/origin
            priority:<level> — if useful (high|medium|low)
          `threads(tags=[a,b])` matches threads that have ALL listed tags.

        Examples:
          agent_bus(tool="fetch", arguments='{"thread": "111", "last": 3, "compact": true}')
          agent_bus(tool="reply", arguments='{"thread": "111", "to": "web", "subject": "Re: topic", "body": "## Reply\\n...", "after_turn": 5, "from_agent": "cursor"}')
          agent_bus(tool="reply", arguments='{"thread": "111", "to": "web", "subject": "Re: long-form handoff", "body": "...", "after_turn": 5, "from_agent": "cursor", "allow_long_body": true}')
          fs(sandbox="cortex", op="write", path="notes/system/threads/review-bug-details.md", content="...")
          agent_bus(tool="post", arguments='{"slug": "review-bug", "to": "cursor", "subject": "Bug found", "body": "Details: cortex:notes/system/threads/review-bug-details.md", "from_agent": "claude-web", "tags": ["project:ulg", "type:bug"]}')
          agent_bus(tool="threads", arguments='{"tags": ["project:claudeburst", "type:bug"]}')
          agent_bus(tool="threads", arguments='{"lifecycle_state": "pending"}')
          agent_bus(tool="create_thread", arguments='{"slug": "my-workflow", "lifecycle_state": "pending", "tags": ["project:ulg"]}')
          agent_bus(tool="update_thread", arguments='{"thread": "553", "tags": ["project:claudeburst", "type:restore"]}')
        """
        from ._agent_tools import parse_dispatch_arguments

        handler = AGENT_BUS_OPS.get(tool)
        if handler is None:
            return {
                "error": f"Unknown agent_bus tool {tool!r}. "
                f"Available: {sorted(AGENT_BUS_OPS.keys())}"
            }
        t_prog, prog_timer = toolprogress_begin("agent_bus", inner_tool=tool)
        err: str | None = None
        try:
            parsed = parse_dispatch_arguments(arguments)
            if parsed is None:
                return {
                    "error": (
                        "arguments must be a JSON-encoded object string "
                        f'(e.g. \'{{"thread": "111"}}\'); got {type(arguments).__name__} '
                        f"that did not parse as a JSON object"
                    )
                }
            if tool == "post":
                # Guardrail C: reconcile the `from` alias and reject
                # continuation-shaped misuse before the unknown-argument gate.
                parsed, misuse = reconcile_post_arguments(parsed)
                if misuse is not None:
                    record(
                        "mcp.agentbus.post.rejected",
                        reason=str(misuse.get("reason", "")),
                    )
                    return misuse
            accepted = set(inspect.signature(handler).parameters)
            unknown = [k for k in parsed if k not in accepted]
            if unknown:
                record(
                    "mcp.agentbus.dispatch.rejected",
                    tool=tool,
                    unknown=",".join(sorted(unknown)),
                )
                return _unknown_arg_error(tool=tool, unknown=unknown, accepted=accepted)
            record("mcp.agentbus.dispatch", tool=tool)
            result = handler(**parsed)
            if (
                isinstance(result, dict)
                and "error" not in result
                and tool in ("post", "reply")
            ):
                result["_next"] = (
                    "If this message records a decision or surfaces an insight, "
                    "seed it as a cortex assert with "
                    'evidence_uris: ["agent-bus:THREAD_ID"]'
                )
            return result
        except Exception as exc:
            err = str(exc)
            raise
        finally:
            toolprogress_end(
                t_prog,
                prog_timer,
                "agent_bus",
                error=err,
                inner_tool=tool,
            )
