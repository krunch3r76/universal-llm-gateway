"""Agent-bus tools — dispatch-style MCP interface to the Agent Bus service.

Package-shadow split of the former ``agent_bus.py`` module. The public import
path ``tools.agent_bus`` is preserved via this ``__init__`` re-export surface.
"""

from __future__ import annotations

import inspect
from typing import TYPE_CHECKING, Any

from mcp_events import record  # re-exported for tests patching tools.agent_bus.record
from mcp_toolprogress import toolprogress_begin, toolprogress_end

from .._agent_bus_author import AUTHOR_AUTOFILL_OPS, reconcile_author_arguments
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
    _add_tags_dispatch,
    _close_dispatch,
    _close_impl,
    _delete_thread_dispatch,
    _delete_thread_impl,
    _delete_turn_dispatch,
    _delete_turn_impl,
    _remove_tags_dispatch,
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
from .request import (
    _request_dispatch,
    _request_impl,
)
from .send import (
    _send_dispatch,
    _send_impl,
)
from .threads import (
    _create_thread_dispatch,
    _create_thread_impl,
    _thread_get_dispatch,
    _threads_dispatch,
    _threads_impl,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from fastmcp import FastMCP

AGENT_BUS_DEPRECATED_OPS: frozenset[str] = frozenset({"post", "reply"})

AGENT_BUS_OPS: dict[str, Callable[..., Any]] = {
    "send": _send_dispatch,
    "request": _request_dispatch,
    "post": _post_dispatch,
    "reply": _reply_dispatch,
    "fetch": _fetch_dispatch,
    "fetch_unread": _fetch_unread_dispatch,
    "get": _get_dispatch,
    "threads": _threads_dispatch,
    "thread_get": _thread_get_dispatch,
    "create_thread": _create_thread_dispatch,
    "close": _close_dispatch,
    "update_thread": _update_thread_dispatch,
    "add_tags": _add_tags_dispatch,
    "remove_tags": _remove_tags_dispatch,
    "update": _update_dispatch,
    "delete_thread": _delete_thread_dispatch,
    "delete_turn": _delete_turn_dispatch,
    "mark_read": _mark_read_dispatch,
    "triage": _triage_dispatch,
    "wait": _wait_dispatch,
}


def advertised_agent_bus_ops() -> tuple[str, ...]:
    """Ops advertised on the wire ``tool`` enum — excludes deprecated post/reply."""
    return tuple(sorted(op for op in AGENT_BUS_OPS if op not in AGENT_BUS_DEPRECATED_OPS))


__all__ = [
    "AGENT_BUS_DEPRECATED_OPS",
    "AGENT_BUS_OPS",
    "advertised_agent_bus_ops",
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
    "_request_dispatch",
    "_request_impl",
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
          Turn bodies MUST be short briefings (target < 2 KB). Prefer durable
          Cortex sidecars over inline walls of text. Sidecar-first is the
          intended usage model; auto-spill is the safety net, not the intended
          model. Preferred path: pass sidecar_content (and optional
          sidecar_slug) on send — the server writes
          cortex://notes/system/threads/<thread_id>-<slug>.md atomically before
          the turn insert and appends a trailing Sidecar: pointer to the body.
          Manual alternative (legacy): fs(sandbox="cortex", op="write",
             path="notes/system/threads/<thread>-<subject>.md", content="...")
          then reference it concisely in the turn body. On send (and legacy
          post/reply; request routes through send): soft overflow above the
          inline soft limit (no caller sidecar_content) is auto-spilled to a
          Cortex sidecar + pointer with HTTP 200 (see
          libs/agent_bus_store/body_auto_spill.py). HTTP 413 is reserved for
          the allow_long_body hard ceiling, spill-write failure, and
          sidecar-content-too-large — not ordinary soft overflow. Workspace
          mirrors (e.g. tmp/reviews) are secondary; point first to the Cortex
          sidecar. Rare exception: allow_long_body=true opts the turn out of auto-spill
          and keeps the body inline up to the hard ceiling when a sidecar would
          break the recipient contract; exceeding that ceiling returns 413.
          Stargate on-behalf pipeline delivery performs the equivalent durable
          sidecar write automatically (see ``async_tracker_delivery/on_behalf.py``).

        Write path — use ``send`` (``post``/``reply`` are legacy aliases until 2026-09-01):

        Operations:
          send          (new_slug XOR thread, to, subject, body, from?, from_agent?, summary?, tags?, enroll_charter_runner?, lifecycle_state?, after_turn?, status?, mark_read?, close?, attachments?, allow_long_body?, sidecar_content?, sidecar_slug?, supersedes_turn?, supersedes_turn_id?) — **primary write op**. Exactly one of new_slug (new thread) or thread (continue) required; supersedes_turn (continue path only) is the same-thread **turn_number** to supersede structurally (deprecated alias supersedes_turn_id = row id, one release cycle). slug uniqueness enforced on new_slug path (409 slug_exists on collision). Tag ``charter-runner`` is **reserved enrollment** — newly adding it requires ``enroll_charter_runner=true`` (422 reserved_enrollment_tag otherwise); keeping/removing never needs the flag. Enrollment auto-stamps spine tag ``role:root``. When sidecar_content is set the server writes cortex://notes/system/threads/<thread_id>-<slug>.md before inserting the turn, appends a trailing Sidecar: pointer to the body, and returns sidecar_uri + sidecar_sha256. sidecar_content cap 256KB. Prefer ``from=``; ``from_agent`` is a permanent alias. When omitted on ``/mcp/life`` or ``/mcp/code``, the server autofills ``web-anthropic`` or ``cursor`` respectively.
          thread_get      (thread) — single ThreadDetail (tags/status/summary/turn_count/…); missing thread → structured error
          add_tags        (thread, tags[], from?, enroll_charter_runner?) — additive tag merge; unspecified tags preserved
          remove_tags     (thread, tags[], from?) — remove listed tags only; other tags preserved
          request       (new_slug XOR thread, to='cursor', subject, body, from?, from_agent?, summary?, tags?, sidecar_content?, sidecar_slug?, desired_model?, desired_effort?, contract?, require_attended?) — life-callable Cursor Auto channel. Injects lane:cursor-auto; arms Auto when a live handler heartbeats (else handler_status=no-auto-handler); returns {thread, turn, handler_status, poll_hint}. ``summary`` = standing ULG so-what title (also fail-soft from body ``so_what:`` / ``ulg_gain:``). require_attended=true (wire or DIRECTIVE body OR) ⇒ terminal status:needs-attended reason=operator_require_attended. ¬ dual-tag lane:life-to-code on degrade. ``contract`` ∈ answer|confer|investigate|implement|verify|execute|propagate|seed — unknown value ⇒ 422 request_contract_unknown before the turn is written; legacy ``consult`` aliases to ``confer`` with a deprecation note. ``execute`` fires ONE tier-M tool op in seat against the allowlist manifest (body: ``tool_op: <tool>.<op>`` + ``effects_expected:`` + optional single-line JSON ``tool_args:``); closeout carries the raw payload under ``tool_payload``. ``propagate`` mints structured propagation ledger rows and coordinates drain-gated ``sync_restart`` via manage.sock (body: ``effects_expected:`` + ``## propagation`` YAML or ``scope: propagation sync_restart <service>``); ``manage.*`` via ``execute`` remains denied. ``seed`` requests a closable work item via the seed path (architecture may be open). Optional ``request_id`` is an idempotency key echoed enqueue→closeout (minted when omitted; a replayed key is refused 422 ``duplicate_request_id``). DIRECTIVE ``deadline: +15m`` (or ISO-8601) terminates a still-queued job ``status:failed reason=expired``. Narrower alternative for approval-gated harnesses: the dedicated ``cursor_request`` tool.
          threads       (status?, tags?, lifecycle_state?, limit?, last?, has_unread?, query?) — list threads; status: active|blocked|waiting|closed|all (default active); tags: AND-filter; lifecycle_state: pending|admitted|delivered|failed (exact match). Default limit=50 when neither limit nor last is set; response includes limit_applied and truncated.
          create_thread (slug, summary?, tags?, enroll_charter_runner?, lifecycle_state?, thread_id?) — create a thread without a turn; use lifecycle_state="pending" for lifecycle-managed threads that will be dispatched later; ``enroll_charter_runner=true`` required to include tag ``charter-runner``
          fetch_unread  (to?, thread?, mark_read?, compact?, active_since?, limit?, all?) — recipient scope (to set, thread unset): enriched per-thread unread digest (slug, last_subject, last_activity_at; default 14d window, limit 50; unwindowed totals in response). thread scope: that thread's full unread turn list (no count cap; compact controls bodies). At least one of to/thread required.
          fetch         (to?, thread?, last?, unread?, compact?, mark_read?, all?)  — get turns; at least one of to/thread required; all=true fetches every turn (no limit); unread=true fetches all unread (last ignored; prefer fetch_unread); last caps windowed fetches (default 10, unread default false); compact default false (bodies projected) — pass compact=true for metadata-only (compact nulls turn bodies, it does not truncate them)
          get           (thread, turn_number)                           — get one specific turn; turn_number may be int or "latest"
          update        (thread, turn_number, body?, append?, subject?) — edit or append to an existing turn while read_at is null; 409 turn_already_acknowledged once marked read (use send(thread=...) for follow-up)
          mark_read     (thread, turn_numbers[] XOR through_turn, agent?) — bulk mark read; through_turn requires agent
          wait          (thread, after_turn?, wait_seconds?, completion?, from_agent?) — server-side short-block until consult posts a bus turn after the pointer (completion=first_reply_from + canonical from_agent; alias-aware), thread closes (completion=thread_closed), or Auto posts a terminal status token (completion=status:done|status:failed|status:needs-attended); wait_seconds clamped <=300 (0=snapshot). Returns {thread_id, complete, status, push_required, suggested_next (object: consult_turn_posted + steps fetch/apply/close when complete and thread still active), qualifying_reply_turn, thread_status, ...}. first_reply_from complete means a consult bus turn exists, not findings applied. Re-call to keep polling — one HTTP call, not a client loop.
          update_thread (thread, status?, summary?, tags?, add_tags?, remove_tags?, enroll_charter_runner?, from_agent?) — patch thread metadata (tags: omit=keep, []=clear, [...]=replace; add_tags/remove_tags are additive and mutually exclusive with tags replace)
          close         (thread, summary?, mark_all_read?)              — close a thread (atomic: marks all turns read by default)
          delete_turn   (thread, turn_number, force?)                   — delete a single turn
          delete_thread (thread, force?)                                — delete an entire thread
          triage        (from?, from_agent?, older_than, status?, action=mark_read|close, dry_run=true, confirm_token?) — bulk inbox hygiene (agent_bus only). Preview with dry_run=true returns candidates + confirm_token; execute with dry_run=false + confirm_token. Floors: mark_read ≥24h, close ≥7d. Cap 50 threads/call. Prefer ``from=``; surface autofill matches send.

        Legacy write ops (deprecated 2026-06-14; omitted from wire ``tool`` enum; still accepted; response includes ``_deprecated``):
          post          (slug, to, subject, body, …) — use send(new_slug=..., ...) instead; removed 2026-09-01.
          reply         (thread, to, subject, body, after_turn, …) — use send(thread=..., after_turn=..., ...) instead; removed 2026-09-01.

        Thread response fields (ThreadDetail):
          id, slug, status, summary, turn_count, unread_count, tags, created_at, updated_at
          bus_lifecycle_state: str | null — lifecycle state for dispatch-managed threads
            (pending → admitted → delivered; null = not lifecycle-managed)
          dispatch_links: list — on single-thread detail only; list_threads responses always carry [] (links are not loaded on list)

        Turn response fields (Turn — returned by fetch, fetch_unread, get, and as
        the created turn inside send):
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

        Tags — thin classification + free-form facets:
          Classification (enforced on write):
            Spine: role:root ⇒ root (standing continuity); absent ⇒ work (default).
              Only role:root is reserved; other role:* tags → 422 unknown_role_tag.
            Enrollment: charter-runner (dual-key; enroll_charter_runner=true to newly
              add). Enrolled ⇒ auto-stamps role:root.
          Facet convention (suggested, not enforced):
            project:<name>   — project scoping (e.g. project:claudeburst)
            type:<kind>      — intent (bug|feature|discussion|review|post-mortem|monitor)
            agent:<name>     — agent ownership/origin
            priority:<level> — if useful (high|medium|low)
          Not classification: bus_lifecycle:*, DB status/bus_lifecycle_state, thread 480.
          `threads(tags=[a,b])` matches threads that have ALL listed tags.

        Examples:
          agent_bus(tool="fetch", arguments='{"thread": "111", "last": 3, "compact": true}')
          agent_bus(tool="request", arguments='{"new_slug": "arm-auto", "to": "cursor", "subject": "Implement X", "body": "TYPE: DIRECTIVE\\ncontract: implement\\n...", "contract": "implement"}')  # arms cursor-auto; poll returned poll_hint with wait. to MUST be "cursor" — never "cursor-auto".
          agent_bus(tool="send", arguments='{"thread": "111", "to": "web", "subject": "Re: topic", "body": "## Reply\\n...", "after_turn": 5, "from": "cursor"}')
          agent_bus(tool="send", arguments='{"new_slug": "review-bug", "to": "cursor", "subject": "Bug found", "body": "Details: cortex:notes/system/threads/review-bug-details.md", "from": "web-anthropic", "tags": ["project:ulg", "type:bug"]}')  # attended cursor seat only — send never arms Auto; use request for that.
          agent_bus(tool="send", arguments='{"thread": "111", "to": "web", "subject": "Re: long-form handoff", "body": "...", "after_turn": 5, "from_agent": "cursor", "allow_long_body": true, "sidecar_content": "# Full spec\\n..."}')
          fs(sandbox="cortex", op="write", path="notes/system/threads/review-bug-details.md", content="...")
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
                "error": (
                    f"Unknown agent_bus tool {tool!r}. "
                    f"Available: {list(advertised_agent_bus_ops())}. "
                    f"Legacy (deprecated): {sorted(AGENT_BUS_DEPRECATED_OPS)}"
                )
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
                    record(
                        "mcp.agentbus.send.rejected",
                        reason=str(alias_error.get("reason", "")),
                    )
                    return alias_error
            if tool in AUTHOR_AUTOFILL_OPS:
                parsed, author_error = reconcile_author_arguments(parsed)
                if author_error is not None:
                    record(
                        "mcp.agentbus.dispatch.rejected",
                        tool=tool,
                        reason=str(author_error.get("reason", "")),
                    )
                    return author_error
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
