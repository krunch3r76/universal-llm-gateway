"""Agent-bus tools — dispatch-style MCP interface to the Agent Bus service.

Package-shadow split of the former ``agent_bus.py`` module. The public import
path ``tools.agent_bus`` is preserved via this ``__init__`` re-export surface.
"""

from __future__ import annotations

import inspect
from typing import TYPE_CHECKING, Any

from mcp_events import record  # re-exported for tests patching tools.agent_bus.record
from mcp_toolprogress import toolprogress_begin, toolprogress_end

from .._agent_bus_post_guard import reconcile_post_arguments, reconcile_send_arguments
from .._agent_tools import JsonArgStr
from .._local_relay import relay as _relay
from ._shared import (
    _FETCH_CONTEXT_CAP,
    _format_agent_bus_error,
    _structured_relay_error,
    _unknown_arg_error,
)
from .fetch import (
    _fetch_dispatch,
    _fetch_impl,
    _fetch_unread_dispatch,
    _fetch_unread_toc_impl,
    _get_dispatch,
    _get_impl,
)
from .lifecycle import (
    _close_dispatch,
    _close_impl,
    _delete_thread_dispatch,
    _delete_thread_impl,
    _delete_turn_dispatch,
    _delete_turn_impl,
    _triage_dispatch,
    _update_thread_dispatch,
    _update_thread_impl,
    _wait_dispatch,
)
from .post_reply import (
    _post_dispatch,
    _post_impl,
    _reply_dispatch,
    _reply_impl,
)
from .read_state import (
    _mark_read_dispatch,
    _resolve_turn_id,
    _update_dispatch,
    _update_impl,
)
from .send import (
    _send_dispatch,
    _send_impl,
)
from .threads import (
    _create_thread_dispatch,
    _create_thread_impl,
    _threads_dispatch,
    _threads_impl,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from fastmcp import FastMCP

AGENT_BUS_OPS: dict[str, Callable[..., Any]] = {
    "send": _send_dispatch,
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
    "triage": _triage_dispatch,
    "wait": _wait_dispatch,
}

__all__ = [
    "AGENT_BUS_OPS",
    "_FETCH_CONTEXT_CAP",
    "_close_dispatch",
    "_close_impl",
    "_create_thread_dispatch",
    "_create_thread_impl",
    "_delete_thread_dispatch",
    "_delete_thread_impl",
    "_delete_turn_dispatch",
    "_delete_turn_impl",
    "_fetch_dispatch",
    "_fetch_impl",
    "_fetch_unread_dispatch",
    "_fetch_unread_toc_impl",
    "_format_agent_bus_error",
    "_get_dispatch",
    "_get_impl",
    "_mark_read_dispatch",
    "_post_dispatch",
    "_post_impl",
    "_relay",
    "_reply_dispatch",
    "_reply_impl",
    "_resolve_turn_id",
    "_send_dispatch",
    "_send_impl",
    "_structured_relay_error",
    "_threads_dispatch",
    "_threads_impl",
    "_triage_dispatch",
    "_unknown_arg_error",
    "_update_dispatch",
    "_update_impl",
    "_update_thread_dispatch",
    "_update_thread_impl",
    "_wait_dispatch",
    "record",
    "register_agent_bus_tools",
]


def register_agent_bus_tools(mcp: FastMCP) -> None:
    """Register the dispatch-style agent_bus tool on the MCP server instance."""

    @mcp.tool(title="Agent Bus")
    def agent_bus(tool: str, arguments: JsonArgStr = "{}") -> Any:
        """Inter-agent message bus — threads, turns, read/reply coordination.

        tool: operation name (see table below)
        arguments: JSON-encoded object string (e.g. '{"thread": "111"}')

        Body convention — SIDECAR-DRIVEN (read before posting):
          Turn bodies MUST be short briefings (target < 2 KB; server enforces a
          hard char limit). Long content — reviews, specs, analysis, handoffs —
          goes in a Cortex sidecar markdown file. Preferred path: pass
          sidecar_content (and optional sidecar_slug) on send — the server writes
          cortex://notes/system/threads/<thread_id>-<slug>.md atomically before
          the turn insert and appends a trailing Sidecar: pointer to the body.
          Manual alternative (legacy): fs(sandbox="cortex", op="write",
             path="notes/system/threads/<thread>-<subject>.md", content="...")
          then reference it concisely in the turn body.
          The turn body then references it concisely:
            "Review complete. Full findings: cortex:notes/system/threads/949-review.md"
          Workspace files (for example tmp/reviews packets) may be mirrors, but
          agent_bus messages should point first to the Cortex sidecar.
          Posting an oversized body returns 413. The sidecar pattern is not a
          workaround for the limit — it IS the intended usage model. Brief body
          + durable sidecar file is always preferred over an inline wall of text.
          Rare exception: pass allow_long_body=true on post/reply only when the
          recipient needs inline long-form content and a sidecar would break the
          communication contract. Stargate on-behalf pipeline delivery performs
          the equivalent durable sidecar write automatically (see
          ``async_tracker_delivery/on_behalf.py``).

        Operations:
          threads       (status?, tags?, lifecycle_state?)              — list threads; status: active|blocked|waiting|closed|all (default active); tags: AND-filter; lifecycle_state: pending|admitted|delivered|failed (exact match)
          create_thread (slug, summary?, tags?, lifecycle_state?, thread_id?) — create a thread without a turn; use lifecycle_state="pending" for lifecycle-managed threads that will be dispatched later
          fetch_unread  (to?, thread?, mark_read?, compact?, active_since?, limit?, all?) — recipient scope (to set, thread unset): enriched per-thread unread digest (slug, last_subject, last_activity_at; default 14d window, limit 50; unwindowed totals in response). thread scope: that thread's full unread turn list (no count cap; compact controls bodies). At least one of to/thread required.
          fetch         (to?, thread?, last?, unread?, compact?, mark_read?, all?)  — get turns; at least one of to/thread required; all=true fetches every turn (no limit); unread=true fetches all unread (last ignored; prefer fetch_unread); last caps windowed fetches (default 10, unread default false); compact default false (bodies projected) — pass compact=true for metadata-only
          get           (thread, turn_number)                           — get one specific turn; turn_number may be int or "latest"
          post          (slug, to, subject, body, from_agent, summary?, attachments?, tags?, allow_long_body?) — start a new thread (atomic: creates thread + first turn). from_agent is REQUIRED — name the seat authoring the turn (e.g. "cursor", "claude-web", "gpt-cursor", "claude-api"); there is no default. DEPRECATED 2026-06-14 — use send(new_slug=..., ...) instead; removed 2026-09-01.
          send          (new_slug XOR thread, to, subject, body, from_agent, summary?, tags?, lifecycle_state?, after_turn?, status?, mark_read?, close?, attachments?, allow_long_body?, sidecar_content?, sidecar_slug?) — unified post/reply surface. Exactly one of new_slug (new thread) or thread (continue) required; slug uniqueness enforced on new_slug path (409 slug_exists on collision). When sidecar_content is set the server writes cortex://notes/system/threads/<thread_id>-<slug>.md before inserting the turn, appends a trailing Sidecar: pointer to the body, and returns sidecar_uri + sidecar_sha256. sidecar_content cap 256KB. from_agent is REQUIRED.
          reply         (thread, to, subject, body, after_turn, from_agent, status?, mark_read?, close?, attachments?, allow_long_body?) — reply to a thread; allow_long_body=true explicitly bypasses the 8k briefing limit for rare inline long-form messages; close=true posts this as the final turn and closes the thread (marks all turns read). from_agent is REQUIRED — name the seat authoring the turn; there is no default. DEPRECATED 2026-06-14 — use send(thread=..., ...) instead; removed 2026-09-01.
          update        (thread, turn_number, body?, append?, subject?) — edit or append to an existing turn while read_at is null; 409 turn_already_acknowledged once marked read (use send/reply for follow-up)
          mark_read     (thread, turn_numbers[] XOR through_turn, agent?) — bulk mark read; through_turn requires agent
          wait          (thread, after_turn?, wait_seconds?, completion?, from_agent?) — server-side short-block until consult posts a bus turn after the pointer (completion=first_reply_from + canonical from_agent; alias-aware) or thread closes (completion=thread_closed); wait_seconds clamped <=60 (0=snapshot). Returns {thread_id, complete, status, push_required, suggested_next (object: consult_turn_posted + steps fetch/apply/close when complete and thread still active), qualifying_reply_turn, thread_status, ...}. first_reply_from complete means a consult bus turn exists, not findings applied. Re-call to keep polling — one HTTP call, not a client loop.
          update_thread (thread, status?, summary?, tags?, from_agent?) — patch thread metadata (tags: omit=keep, []=clear, [...]=replace)
          close         (thread, summary?, mark_all_read?)              — close a thread (atomic: marks all turns read by default)
          delete_turn   (thread, turn_number, force?)                   — delete a single turn
          delete_thread (thread, force?)                                — delete an entire thread
          triage        (from_agent, older_than, status?, action=mark_read|close, dry_run=true, confirm_token?) — bulk inbox hygiene (agent_bus only). Preview with dry_run=true returns candidates + confirm_token; execute with dry_run=false + confirm_token. Floors: mark_read ≥24h, close ≥7d. Cap 50 threads/call.

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
        from .._agent_tools import (
            dispatch_arguments_error,
            parse_dispatch_arguments,
        )

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
                return dispatch_arguments_error(arguments, example='{"thread": "111"}')
            if tool == "post":
                parsed, misuse = reconcile_post_arguments(parsed)
                if misuse is not None:
                    record(
                        "mcp.agentbus.post.rejected",
                        reason=str(misuse.get("reason", "")),
                    )
                    return misuse
            if tool == "send":
                parsed, alias_error = reconcile_send_arguments(parsed)
                if alias_error is not None:
                    return alias_error
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
                and tool in ("post", "reply", "send")
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
