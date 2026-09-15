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
from .branch_associations import (
    _branch_associate_dispatch,
    _branch_current_dispatch,
)
from .entity_mint import (
    _entity_mint_dispatch,
)
from .fetch import (
    _fetch_dispatch,
    _fetch_impl,
    _fetch_unread_dispatch,
    _fetch_unread_toc_impl,
    _get_dispatch,
    _get_impl,
)
from .friction_file import (
    _friction_file_dispatch,
)
from .graph_write import (
    _graph_write_dispatch,
)
from .hop import (
    _hop_dispatch,
)
from .lane_associations import (
    _lane_bind_dispatch,
    _lane_current_dispatch,
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
    "hop": _hop_dispatch,
    "substrate_graph_write": _graph_write_dispatch,
    "substrate_friction_file": _friction_file_dispatch,
    "substrate_entity_mint": _entity_mint_dispatch,
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
    "branch_associate": _branch_associate_dispatch,
    "branch_current": _branch_current_dispatch,
    "lane_bind": _lane_bind_dispatch,
    "lane_current": _lane_current_dispatch,
}


def advertised_agent_bus_ops() -> tuple[str, ...]:
    """Ops advertised on the wire ``tool`` enum — excludes deprecated post/reply."""
    return tuple(
        sorted(op for op in AGENT_BUS_OPS if op not in AGENT_BUS_DEPRECATED_OPS)
    )


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
    "_entity_mint_dispatch",
    "_friction_file_dispatch",
    "_graph_write_dispatch",
    "_hop_dispatch",
    "_lane_bind_dispatch",
    "_lane_current_dispatch",
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
        """Inter-agent bus — `tool` op + JSON `arguments` string.

**Wire:** `tool` = op name · `arguments` = JSON object string.

**Sidecar-first bodies:** turn bodies target <2KB. Prefer `sidecar_content` (+ optional `sidecar_slug`) on send — server writes `cortex://notes/system/threads/<thread_id>-<slug>.md` atomically, appends `Sidecar:` pointer; returns `sidecar_uri` + `sidecar_sha256` (cap **256KB**). Soft overflow without caller sidecar → auto-spill HTTP 200; **413** = hard ceiling / spill failure / sidecar too large / `allow_long_body` ceiling breach. `allow_long_body=true` opts out of auto-spill.

**Author autofill:** prefer `from=`; `from_agent` alias permanent. Omitted on `/mcp/life` → `web-anthropic`; `/mcp/code` → `cursor`.

---

**Ops (primary — use `send`, not post/reply):**

`send` — **primary write**. `(new_slug XOR thread)` + `to` + `subject` + `body` + optional fields. `new_slug` collision → **409 `slug_exists`**. `supersedes_turn` continue-only (alias `supersedes_turn_id` deprecated one cycle). Tag `charter-runner` reserved — newly add requires `enroll_charter_runner=true` else **422 `reserved_enrollment_tag`**. Enrollment auto-stamps `role:root`.

`request` — `(new_slug XOR thread)` + `to='cursor'` (must be literal `cursor`, never `cursor-auto`) + `subject` + `body` + optional `prompt_uri`|`advisor_brief`. Injects `lane:cursor-auto`; arms when Auto handler heartbeats (else `handler_status=no-auto-handler`); returns `{thread, turn, handler_status, poll_hint}`. `lane`∈{`A`,`B`} GIW checkout isolation; `workspace` satellite name; `parent_thread`+`lane_role` both required when either supplied. `desired_effort` omit/`auto` → judgment `xhigh`, mechanical `medium`. `contract`∈{`answer`,`confer`,`ask`,`investigate`,`implement`,`verify`,`execute`,`propagate`,`seed`,`recon`} — unknown → **422 `request_contract_unknown`**; `consult` aliases `confer`. `implement`|`investigate` body requires `vision:` else admit **`vision_field_missing`**. `require_attended` → terminal `status:needs-attended`. `execute`: body `tool_op:` + `effects_expected:` + optional `tool_args:`. `propagate`: ledger + drain-gated `sync_restart`. `request_id` idempotency — replay → **422 `duplicate_request_id`**. `deadline:` in DIRECTIVE → queued job `status:failed reason=expired`. Narrow harness: dedicated `cursor_request` tool.

`hop` — `thread` + `reason` + optional `cse_chat_url`|`cse_registration_id`|`desired_model`|`desired_effort`|`request_id`|`after_turn`|`subject`; TYPE:CONTINUITY_HANDOFF; `continuity_hop=true`; returns armed receipt + `successor` (¬`status:done`). Hop-before-healthy → degrade not arm.

`substrate_graph_write` — `entity_id` + `claim` → assert payload; **422 `graph_write_entity_required`|`graph_write_claim_required`**; ¬mint 404 · ¬bus turn · ¬contract token.

`substrate_friction_file` — (`owner`|`service`) + (`note`|`claim`) → friction payload; **422 `friction_file_owner_required`|`friction_file_note_required`**; ¬mint 404 · ¬contract token.

`substrate_entity_mint` — (`id`|`entity_id`) + (`type`|`entity_type`) + (`name`|`title`); retention/Option-C/density_triage → named **422**; ¬rich-seed · ¬contract token.

`lane_bind` — `thread` + `parent_thread` + `lane_role`; roles∈{`sub_mission`,`hop`,`spillover`,`dispatch`,`side`,`parallel`} (no root); ¬contract token.

`lane_current` — `thread` → current parentage; `state=none` if never bound.

`thread_get` — `thread` → ThreadDetail.

`threads` — filter by `status`∈{`active`,`blocked`,`waiting`,`closed`,`all`} (default active), `tags` AND, `lifecycle_state`, `last` default **50**; returns `limit_applied`, `truncated`.

`create_thread` — `slug` + optional; `lifecycle_state=pending` for dispatch-managed; `enroll_charter_runner` for `charter-runner` tag.

`fetch_unread` — requires `to` and/or `thread`; recipient scope default 14d window limit **50**; thread scope uncapped list.

`fetch` — requires `to` and/or `thread`; `last` default **10**; `unread=true` ignores `last`; `all=true` no cap; `compact=true` nulls bodies (metadata-only).

`get` — `thread` + `turn_number` (int or `"latest"`).

`update` — edit while `read_at` null; **409 `turn_already_acknowledged`** once read.

`mark_read` — `turn_numbers[]` **XOR** `through_turn` (+ `agent` if through_turn).

`wait` — server block ≤**60**s (`wait_seconds` clamped); `completion`∈{`first_reply_from`,`thread_closed`,`status:done|failed|needs-attended`}; returns `{complete, status, push_required, suggested_next, …}`.

`update_thread` — patch metadata; `tags` omit=keep, `[]`=clear, `[…]`=replace; `add_tags`/`remove_tags` XOR `tags` replace.

`close` — atomic; `mark_all_read` default true.

`delete_turn` / `delete_thread` — optional `force`.

`triage` — `older_than` + `action`∈{`mark_read`,`close`}; preview `dry_run=true` + `confirm_token`; execute `dry_run=false`; floors mark_read ≥**24h**, close ≥**7d**; cap **50** threads/call; bad token → **409 `confirm_token_invalid|expired|filter_mismatch`**. agent_bus only.

**Legacy (deprecated 2026-06-14, omitted from wire enum, remove 2026-09-01):** `post` → `send(new_slug=…)` · `reply` → `send(thread=…, after_turn=…)`.

---

**Enums:** TurnStatus (per-turn): `open`|`resolved`|`superseded`|`waiting`. ThreadStatus: `active`|`blocked`|`waiting`|`closed`. Tags: spine `role:root` reserved — other `role:*` → **422 `unknown_role_tag`**. Facets suggested: `project:`|`type:`|`agent:`|`priority:`.

**ThreadDetail fields:** `id, slug, status, summary, turn_count, unread_count, tags, created_at, updated_at, bus_lifecycle_state, dispatch_links` (detail only).

Depth: `agent_skill:agent-bus-discipline`.
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
