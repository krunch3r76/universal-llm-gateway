"""Narrow cursor-auto request lane — dedicated MCP tool for approval gating.

The web-claude MCP harness gates tool approval at *registered-tool* granularity,
not at the ``arguments.tool`` sub-op level. Because the unified ``agent_bus``
tool bundles destructive ops (``delete_thread``, ``close``, ``triage``) with
``request``, an operator cannot write an allow-by-name rule that covers only
the sanctioned unattended cursor-auto lane. This module registers
``cursor_request``, exposing ONLY the ``request`` op and delegating to
``_request_dispatch`` — no logic is duplicated.

Registered on life and code surfaces alongside ``agent_bus_read``.
"""

from __future__ import annotations

import inspect
from typing import TYPE_CHECKING, Any

from mcp_events import record
from mcp_toolprogress import toolprogress_begin, toolprogress_end

from ._agent_bus_author import reconcile_author_arguments
from .agent_bus import _request_dispatch

# Cached once at import — runtime inspect.signature breaks under test mocks.
_REQUEST_DISPATCH_PARAMS: frozenset[str] = frozenset(
    inspect.signature(_request_dispatch).parameters
)

if TYPE_CHECKING:
    from fastmcp import FastMCP

# Caller-facing wire fields only — ``to`` is fixed to ``cursor``; no tool/op discriminator.
CALLER_FIELDS: frozenset[str] = frozenset(
    {
        "thread",
        "new_slug",
        "subject",
        "body",
        "contract",
        "desired_model",
        "desired_effort",
        "escalation",
        "from_agent",
        "from",
        "summary",
        "tags",
        "sidecar_content",
        "sidecar_slug",
        "require_attended",
        "after_turn",
        "lane",
        "workspace",
        "parent_thread",
        "lane_role",
        "request_id",
        "cse_registration_id",
        "cse_chat_url",
    }
)


def _unknown_caller_error(unknown: list[str]) -> dict[str, Any]:
    return {
        "error": (
            f"cursor_request: unsupported argument(s): "
            f"{', '.join(sorted(unknown))}. "
            f"Accepted: {sorted(CALLER_FIELDS)}"
        ),
    }


def _dispatch_cursor_request(parsed: dict[str, Any]) -> Any:
    """Validate caller dict, reconcile author, delegate to ``_request_dispatch``."""
    unknown = [k for k in parsed if k not in CALLER_FIELDS]
    if unknown:
        record(
            "mcp.agentbus.dispatch.rejected",
            tool="request",
            surface="cursor_request",
            unknown=",".join(sorted(unknown)),
        )
        return _unknown_caller_error(unknown)

    parsed, author_error = reconcile_author_arguments(parsed)
    if author_error is not None:
        record(
            "mcp.agentbus.dispatch.rejected",
            tool="request",
            surface="cursor_request",
            reason=str(author_error.get("reason", "")),
        )
        return author_error

    accepted_dispatch = _REQUEST_DISPATCH_PARAMS
    dispatch_kwargs = {k: v for k, v in parsed.items() if k in accepted_dispatch}
    dispatch_kwargs["to"] = "cursor"
    record("mcp.agentbus.dispatch", tool="request", surface="cursor_request")
    return _request_dispatch(**dispatch_kwargs)


def register_cursor_request_tool(mcp: FastMCP) -> None:
    """Register the narrow ``cursor_request`` tool on the MCP server."""

    def cursor_request(
        subject: str,
        body: str,
        new_slug: str | None = None,
        thread: str | None = None,
        from_agent: str = "",
        summary: str | None = None,
        tags: list[str] | None = None,
        sidecar_content: str | None = None,
        sidecar_slug: str | None = None,
        desired_model: str = "auto",
        desired_effort: str = "auto",
        escalation: str | None = None,
        contract: str = "answer",
        require_attended: bool = False,
        after_turn: int = 0,
        lane: str | None = None,
        workspace: str | None = None,
        parent_thread: str | None = None,
        lane_role: str | None = None,
        request_id: str | None = None,
        cse_registration_id: str | None = None,
        cse_chat_url: str | None = None,
    ) -> Any:
        """Sanctioned unattended cursor-auto lane — `agent_bus` `request` only; `to=cursor` fixed (¬caller param). Exactly one of `new_slug`|`thread` required.

Returns `{thread, turn, handler_status, poll_hint}` — poll terminal via `poll_hint`, ¬client loop. Author: prefer `from_agent=`; surface autofill life→`web-anthropic`, code→`cursor`. Optional `cse_registration_id`|`cse_chat_url` (same as `agent_bus.request`).

**Contract:** `contract`∈{`answer`,`confer`,`ask`,`investigate`,`implement`,`verify`,`execute`,`propagate`,`seed`,`recon`} — unknown → **422** before turn write; `consult` aliases `confer`.

**`desired_effort`:** omit/`auto` → judgment contracts `xhigh`, mechanical `medium`; explicit rung honored.

**`lane`:** optional GIW checkout. In-repo implement uses lane B — pass `B`. Omit is not the implement default: empty `files_expected` + omit → `select_lane` Lane A (`opt_out`). Distinct from `lane_role`. **`workspace`:** optional satellite (`SATELLITES.txt`); omit = hub. **`parent_thread`+`lane_role`:** both required when either supplied.

**Admit gates:** `contract`∈{`implement`,`investigate`} ⇒ DIRECTIVE body MUST include `vision:` else **`vision_field_missing`** at admit (pre-model). `require_attended` (wire or body OR) ⇒ terminal **`status:needs-attended`** + one recommended answer.

**`contract=implement` admit:** `handoff=pure-mechanical` unless body has line-start `RULING` / `RULING AC` (optional `AC<n> —` prefix) — mid-sentence `RULING` ¬ sufficient. SOT: `agent_skill:directive-authoring-standard`.

**Mission negotiation (`contract=confer` only):** TYPE:DIRECTIVE + closed `negotiation_phase`∈{`proposal`,`counter`,`agree`,`ratify`} + `negotiation_id`, `revision`, `in_reply_to_turn`, `proposal_hash`, mission fields, `idle_deadline` in **body** only; Auto replies TYPE:DISPOSITION + closed `negotiation.*` vocab.

**Standing seat posture:** ∀ clear DIRECTIVE: front-door Q1/Q2 bind silently ∧ execute — ¬route/ack poll. ∀ confer/explicit ask: advise with codebase judgment. ∀ operator-only gate: `status:needs-attended`. SOT: `decision:operator-request-front-door`.

**Life coding aperture:** coding interest → `contract=ask` first (omit `desired_model`/`escalation`/`workspace` unless satellite). ¬ sequential `fs`/`rag` as unknown-loci hunter — use `cursor_request(ask|recon)`. In-seat `answer` executes nothing — re-issue `ask`. Index: `document:life-coding-playbook`.

**CDP window (web-anthropic):** life ¬`team_dispatch` — commission cursor-auto via this tool; Auto fires `team_dispatch(model=cdp/opus-5, …)` on **same** private request lane. ¬ mint second private request lane. ¬ `cse_session(followup)` for Customize skill refresh. CLOSEOUT quotes `execution_id` + `poll_hint`.

**Conductor commission:** `investigate` + `lane=B` — packet/nest table: `agent_skill:conductor`.

**Deploy/live:** `landed≠live` = process ¬restarted, never ¬committed; `live@<sha>` needs commit-before-restart + `code_ref_satisfied` + dirty disclosure. SOT: `decision:checkout-disk-is-executable`.

**Codework lanes:** slash commands = attended IDE wrappers only; headless loads skill from DIRECTIVE. `contract=seed` → `work-item-seed-path`; todo codework → `implement`|`investigate`|`verify` + `abstraction-layering` at highest open G1–G6.

**CLOSEOUT shape (by contract):** answer→disposition:answered; confer→recommendation; ask→≤12 lines + file:line; investigate→findings; implement→changes+AC; verify→verdict; execute→tier-M payload; propagate→ledger+restart; seed→todo slug; recon→recon_core.

**Second read (advisory):** implement|investigate|verify may append `## SECOND READ` by `cursor/claude-opus-5` — OBSERVATION only, ¬gate authority. Knobs: `CURSOR_AUTO_REFLEX_ENABLED`, `_BUDGET`, `_SAMPLE_EVERY`, `_MODEL`, `_EFFORT`, `_TIMEOUT_S`.

Claude.ai: scheduling_trigger = option; Authorize_prompt ⇒ operator approves (⊃ schedule).

Depth: `agent_skill:cdp-operator-proxy` · `agent_skill:life-coding-playbook` · `agent_skill:conductor` · `agent_skill:directive-authoring-standard`.
        """
        t_prog, prog_timer = toolprogress_begin("cursor_request")
        err: str | None = None
        try:
            parsed: dict[str, Any] = {
                "subject": subject,
                "body": body,
                "desired_model": desired_model,
                "desired_effort": desired_effort,
                "escalation": escalation,
                "contract": contract,
                "require_attended": require_attended,
                "after_turn": after_turn,
            }
            if lane is not None:
                parsed["lane"] = lane
            if workspace is not None:
                parsed["workspace"] = workspace
            if parent_thread is not None:
                parsed["parent_thread"] = parent_thread
            if lane_role is not None:
                parsed["lane_role"] = lane_role
            if request_id is not None:
                parsed["request_id"] = request_id
            if cse_registration_id is not None:
                parsed["cse_registration_id"] = cse_registration_id
            if cse_chat_url is not None:
                parsed["cse_chat_url"] = cse_chat_url
            if new_slug is not None:
                parsed["new_slug"] = new_slug
            if thread is not None:
                parsed["thread"] = thread
            if from_agent:
                parsed["from_agent"] = from_agent
            if summary is not None:
                parsed["summary"] = summary
            if tags is not None:
                parsed["tags"] = tags
            if sidecar_content is not None:
                parsed["sidecar_content"] = sidecar_content
            if sidecar_slug is not None:
                parsed["sidecar_slug"] = sidecar_slug
            return _dispatch_cursor_request(parsed)
        except Exception as exc:
            err = str(exc)
            raise
        finally:
            toolprogress_end(t_prog, prog_timer, "cursor_request", error=err)

    mcp.tool(
        title="Cursor Auto Request",
        description=cursor_request.__doc__ or "",
    )(cursor_request)

    def operator_request(
        subject: str,
        body: str,
        new_slug: str | None = None,
        thread: str | None = None,
        from_agent: str = "",
        summary: str | None = None,
        tags: list[str] | None = None,
        sidecar_content: str | None = None,
        sidecar_slug: str | None = None,
        desired_model: str = "auto",
        desired_effort: str = "auto",
        escalation: str | None = None,
        contract: str = "answer",
        require_attended: bool = False,
        after_turn: int = 0,
        lane: str | None = None,
        workspace: str | None = None,
        parent_thread: str | None = None,
        lane_role: str | None = None,
        request_id: str | None = None,
        cse_registration_id: str | None = None,
        cse_chat_url: str | None = None,
    ) -> Any:
        """Recipient-neutral approval-gated operator request lane."""
        return cursor_request(
            subject=subject,
            body=body,
            new_slug=new_slug,
            thread=thread,
            from_agent=from_agent,
            summary=summary,
            tags=tags,
            sidecar_content=sidecar_content,
            sidecar_slug=sidecar_slug,
            desired_model=desired_model,
            desired_effort=desired_effort,
            escalation=escalation,
            contract=contract,
            require_attended=require_attended,
            after_turn=after_turn,
            lane=lane,
            workspace=workspace,
            parent_thread=parent_thread,
            lane_role=lane_role,
            request_id=request_id,
            cse_registration_id=cse_registration_id,
            cse_chat_url=cse_chat_url,
        )

    mcp.tool(
        title="Operator Request",
        description=operator_request.__doc__ or "",
    )(operator_request)
