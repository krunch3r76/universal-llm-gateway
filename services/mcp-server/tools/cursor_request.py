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

    @mcp.tool(title="Cursor Auto Request")
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
        desired_effort: str = "medium",
        escalation: str | None = None,
        contract: str = "answer",
        require_attended: bool = False,
        after_turn: int = 0,
    ) -> Any:
        """Sanctioned unattended cursor-auto request lane (agent_bus request only).

        Posts a directive to the Cursor Auto handler. Recipient ``to`` is always
        ``cursor`` — it is not a caller parameter. Exactly one of ``new_slug``
        (new thread) or ``thread`` (continue) is required.

        **Standing seat posture (FOL)**

        ∀ clear DIRECTIVE: front-door Q1/Q2 bind silently ∧ execute — ¬ route/ack poll.
        ∀ confer/explicit ask: advise with codebase judgment — ¬ invent front-door fork.
        ∀ operator-only gate: terminal status:needs-attended + one recommended answer.
        SOT: decision:operator-request-front-door.

        Claude.ai: scheduling_trigger? = option; Authorize_prompt ⇒ operator_approves
        (⊃ schedule). Expect click — ¬ bypass.

        NEW_CDP_WINDOW(web-anthropic): context_reset ∨ Customize_skills_refresh ⇒
        handoff_prompt required; continuity on SAME private request lane — ¬ second lane.

        Sync: plugin_install ∧ per-slug Customize sync ∈ Auto capabilities — offer/fire,
        ¬ defer to IDE lead. Bulk census = slow ⇒ named slugs only. IDE restart ⇒ operator.

        Deploy / live (BINDING — decision:checkout-disk-is-executable):
        sync_restart / host / gateway / MCP load the live shared checkout on disk —
        committed or not. landed≠live = process ¬restarted, never ¬committed.
        Commit is git workflow only, not the edited→running gate. ¬ frame
        served≠HEAD after dirty-checkout restart as illicit live-ahead-of-HEAD.

        **Contract vocabulary**

        ``contract`` ∈ answer | confer | investigate | implement | verify | execute |
        propagate | seed. Unknown ⇒ 422 before turn write. ``consult`` aliases confer.

        **Expected return shape (per contract)**

        | contract | CLOSEOUT carries |
        | answer | disposition:answered + inline relay |
        | confer | codebase-grounded recommendation |
        | investigate | findings / nested dispatch summary |
        | implement | file changes + AC evidence |
        | verify | verification verdict + evidence |
        | execute | one tier-M op raw payload (body: tool_op + effects_expected) |
        | propagate | propagation ledger + drain-gated restart status |
        | seed | todo slug + consult URI (if any) + /layer entry gate |

        **Second read (advisory — may appear on any nested-contract CLOSEOUT)**

        On implement | investigate | verify, Auto may append a ``## SECOND READ``
        block: a bounded read-only pass by ``cursor/claude-opus-5`` over the
        executor's own §2 closeout, answering evidence / likeliest-error /
        what's-missing. It is stamped ``second_read(by=…, ref=…, trigger=…)``
        and is an OBSERVATION, never a ratification — it does not raise or lower
        the envelope ``status:`` and carries no gate authority. Absent block ⇒
        no trigger fired or budget spent, ¬ a clean bill of health.

        Triggers: executor failed · partial/blocked status · ac_verdict miss ·
        non-empty open forks · sensitive paths (libs/, .cursor/, cursor-plugins/,
        config/*.yaml) on write contracts · sparse DIRECTIVE density · every Nth
        job. Per-thread budget caps spend. Knobs: ``CURSOR_AUTO_REFLEX_ENABLED``,
        ``_BUDGET``, ``_SAMPLE_EVERY``, ``_MODEL``, ``_EFFORT``, ``_TIMEOUT_S``.

        Returns ``{thread, turn, handler_status, poll_hint}``. Poll terminal status
        via returned ``poll_hint`` — not a client loop.

        Author: prefer ``from_agent=``; surface autofill on ``/mcp/life`` or
        ``/mcp/code`` when omitted (``web-anthropic`` or ``cursor`` respectively).
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
