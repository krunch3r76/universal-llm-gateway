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
        """Inter-agent bus. `tool` = op name. `arguments` = JSON object string. Prefer `from=`. Bodies target <2KB; use `sidecar_content` (cap 256KB) or auto-spill. Hard ceiling / spill failure → **413**. `allow_long_body=true` opts out of spill. Omitted author: life→`web-anthropic`, code→`cursor`.

**send** (primary write): XOR `new_slug`|`thread` + `to` + `subject` + `body`. Slug collision → **409 `slug_exists`** (body includes `created_thread`). `charter-runner` needs `enroll_charter_runner=true` else **422 `reserved_enrollment_tag`**. `parent_thread`+`lane_role` are both-or-neither.

**request:** XOR `new_slug`|`thread`, `to` literal `cursor`. Returns `{thread, turn, auto_handler_status, job_admission, poll_hint}`. `auto_handler_status` is the handler heartbeat; `job_admission.outcome` is this job. Unknown contract → **422 `request_contract_unknown`** (`consult` aliases `confer`). `implement`|`investigate` need body `vision:` else **`vision_field_missing`**. `require_attended` → `status:needs-attended`. Replay → **422 `duplicate_request_id`**. Narrow path: `cursor_request`.

**hop:** `thread` + `reason`. Returns `successor`, not `status:done`.

**substrate_graph_write:** `entity_id` + `claim` or **422 `graph_write_entity_required`|`graph_write_claim_required`**.

**substrate_friction_file:** (`owner`|`service`) + (`note`|`claim`) or **422 `friction_file_owner_required`|`friction_file_note_required`**.

**substrate_entity_mint:** (`id`|`entity_id`) + (`type`|`entity_type`) + (`name`|`title`).

**lane_bind:** `thread` + `parent_thread` + `lane_role`∈{`sub_mission`,`hop`,`spillover`,`dispatch`,`side`,`parallel`}.

**lane_current** · **thread_get** · **threads** (`last` default 50) · **create_thread** · **fetch_unread** (needs `to` and/or `thread`) · **fetch** (`compact=true` nulls bodies) · **get** (`turn_number` or `"latest"`).

**update:** only while unread; else **409 `turn_already_acknowledged`**.

**mark_read:** XOR `turn_numbers`|`through_turn`.

**wait:** block ≤60s. `completion`∈{`first_reply_from`,`thread_closed`,`status:done|failed|needs-attended`}.

**update_thread:** `tags` omit=keep, `[]`=clear, `[…]`=replace. **close** marks all read by default. **delete_turn** / **delete_thread** take optional `force`.

**triage:** `dry_run=true` + `confirm_token` previews; floors mark_read ≥24h, close ≥7d; cap 50. Bad token → **409 `confirm_token_invalid|expired|filter_mismatch`**.

**Legacy:** `post`/`reply` are not on the wire enum. Use `send`. Other `role:*` than `role:root` → **422 `unknown_role_tag`**.

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
