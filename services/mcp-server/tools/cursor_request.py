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
        desired_effort: str = "medium",
        escalation: str | None = None,
        contract: str = "answer",
        require_attended: bool = False,
        after_turn: int = 0,
        lane: str | None = None,
        parent_thread: str | None = None,
        lane_role: str | None = None,
        request_id: str | None = None,
        cse_registration_id: str | None = None,
        cse_chat_url: str | None = None,
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

        NEW_CDP_WINDOW(web-anthropic): life seat **cannot** call ``team_dispatch`` —
        commission **cursor-auto** via ``cursor_request`` (same tool as every DIRECTIVE).
        Cursor-auto fires ``team_dispatch(model=cdp/opus-5, purpose=operator-proxy|mission,
        dispatch_thread_id=<SAME private request lane>)`` with your ``handoff_prompt``.
        Triggers: episode/mission continuity pickup · Customize skill body went live ·
        stale context reset · predecessor ``MISSION_CLOSEOUT`` named next operator window.
        **¬** mint a second private ``request`` lane. **¬** warm ``cse_session(followup)``
        when chips/MCP/context need refresh — follow-up does not reload Customize skills.
        CLOSEOUT must quote ``execution_id`` + ``poll_hint`` (or honest transport halt).
        Predecessor stream may end only after successor launch is confirmed (inv 30).

        COMMISSION_CONDUCTOR(web-anthropic): to hand a multi-step mission to an
        autonomous cursor-sdk conductor instead of driving every step yourself via
        the DIRECTIVE loop, this is an ordinary request — **no dedicated contract
        token exists or is needed**. Fire with ``contract="investigate"``,
        ``desired_model="cursor/grok-4.6"`` (T1 default — Cursor Models pool;
        ``sonnet-5`` / ``opus-5`` are Other Models pins, T2/T3 only), ``lane="B"``
        (**wire parameter, not packet prose** — an omitted ``lane=`` resolves to
        Lane A/shared-master regardless of what the body says; only name Lane A
        when the mission itself is T0-mechanical single-locus), and
        ``desired_effort="xhigh"``. ``lane="B"`` is a worktree requirement, not a
        label: GIW mints or inherits an isolated tree, or returns 422
        ``CURSOR_LANE_B_WORKTREE_MISSING`` — it does not silently admit on
        shared master. ``investigate`` resolves to
        ``handoff_contract=light-bounded``, so the mechanical-executor redirect
        (Composer-only) never fires — that redirect is scoped to ``implement``.
        Body MUST include a ``vision:`` line (admit body gate above) and the full
        six-block conductor packet (root thread/charter/scoreboard URIs, the
        mandatory literal line ``Use the conductor skill — nest specialists; ¬
        hand-code mechanical G-rows; cost tier from this skill.``) — see
        agent_skill:conductor for packet shape + tier table. Effort knobs are
        gated by the model card (``cursor_capabilities``): accepted rungs pass
        (Sonnet 5 / Opus through ``max``, Grok through ``xhigh``); above-card
        values degrade and ``resolved_effort`` on the admit reply reports the
        clamp. Hop-cadence's in-flight probe checks claimed jobs by home
        lane as well as literal thread-id, so committing onto a thread other than
        this DIRECTIVE's own (``dispatch_thread_id`` = a mission root, the usual
        conductor shape) correctly inhibits an automatic hop while it runs
        (fixed + unit-tested 2026-08-15; not yet confirmed against a live
        commission — poll the target thread yourself until that confirmation
        lands). Separately, a nested dispatch's own poll loop times out at 3600s
        server-side — a conductor mission genuinely running past an hour gets
        marked failed there even while the bus-visible work continues; unfixed,
        watch missions approaching that age.

        Sync: plugin_install ∧ per-slug Customize sync ∈ Auto capabilities — offer/fire,
        ¬ defer to IDE lead. Bulk census = slow ⇒ named slugs only. IDE restart ⇒ operator.

        Deploy / live (BINDING — decision:checkout-disk-is-executable):
        sync_restart / host / gateway / MCP load the live shared checkout on disk —
        committed or not. landed≠live = process ¬restarted, never ¬committed.
        Commit is git workflow only, not the edited→running gate. A live@<sha>
        claim is stronger: commit deployment paths before restart, then prove
        code_ref_satisfied + identity movement and disclose dirty paths. ¬ frame
        served≠HEAD after dirty-checkout restart as illicit live-ahead-of-HEAD.

        **Contract vocabulary**

        ``contract`` ∈ answer | confer | investigate | implement | verify | execute |
        propagate | seed | recon. Unknown ⇒ 422 before turn write. ``consult`` aliases confer.

        **Mission negotiation (body-level, ``contract=confer`` only)**

        Pre-birth async negotiation uses the existing ``TYPE: DIRECTIVE`` envelope with
        a closed ``negotiation_phase: proposal|counter|agree|ratify`` field plus
        ``negotiation_id``, ``revision``, ``in_reply_to_turn``, ``proposal_hash``, mission
        payload fields, and ``idle_deadline``. Auto replies with ``TYPE: DISPOSITION``
        and a closed ``negotiation.*`` vocabulary. No new MCP wire token is introduced;
        negotiation fields ride in ``body`` only. Ordinary DIRECTIVEs without
        ``negotiation_phase`` are unchanged.

        ``lane``: optional GIW checkout-isolation ``A`` | ``B``. Omit for
        current ``select_lane`` defaults. Distinct from ``lane_role``.

        **Admit body gate (implement / investigate)**

        ``contract`` ∈ {implement, investigate} ⇒ DIRECTIVE body MUST include a
        ``vision:`` line or Auto blocks at admit (``vision_field_missing``) before
        a model runs. ``vision: mechanical — <reason>`` suffices for tool ops.
        See agent_skill:cdp-operator-proxy.

        **Judgment marker (implement admit)**

        ``contract=implement`` admits ``handoff=pure-mechanical`` unless the body
        carries an admit-visible marker: line-start ``RULING`` / ``RULING AC``
        (optional ``AC<n> — `` prefix, optional bullet / heading / bold).
        Mid-sentence ``RULING`` does not raise. A judgment AC written any other
        way skips the reasoning-posture preamble AND redirects a pinned reasoning
        model onto Composer. Coverage on agent-bus:9470: 1 of 13 implement
        bodies raise today. Convention SOT: agent_skill:directive-authoring-standard.

        **Codework lanes — IDE command wraps skill (BINDING)**

        Slash commands are attended-IDE wrappers only. cursor-sdk / cursor-auto /
        charter dispatches **never** invoke ``/commands`` — they load the skill slug
        from the DIRECTIVE body or episode BRIEFING.

        | IDE command | Headless skill (machinery SOT) |
        |---|---|
        | ``/work-item-seed`` | ``work-item-seed-path`` |
        | ``/layer`` | ``abstraction-layering`` |

        Mint path: wire ``contract=seed`` (or body ``Use the work-item-seed-path
        skill``). Codework on an existing todo: ``implement`` | ``investigate`` |
        ``verify`` + body ``Use the abstraction-layering skill`` at highest open
        G1–G6 gate — same lane as ``/layer``, not a separate wire token.

        **Expected return shape (per contract)**

        | contract | CLOSEOUT carries |
        | answer | disposition:answered + inline relay |
        | confer | codebase-grounded recommendation |
        | investigate | findings / nested dispatch summary |
        | implement | file changes + AC evidence (codework: ``abstraction-layering`` lane) |
        | verify | verification verdict + evidence (codework: ``abstraction-layering`` G6) |
        | execute | one tier-M op raw payload (body: tool_op + effects_expected) |
        | propagate | propagation ledger + drain-gated restart status |
        | seed | todo slug + consult URI (if any) + ``abstraction-layering`` entry gate |
        | recon | recon_core findings (+ optional recon_extra) |

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
        ``cse_registration_id`` / ``cse_chat_url`` are optional CSE stamps
        (same kwargs as ``agent_bus.request``). Omitted empty-wire still binds
        when census N=1.
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
        desired_effort: str = "medium",
        escalation: str | None = None,
        contract: str = "answer",
        require_attended: bool = False,
        after_turn: int = 0,
        lane: str | None = None,
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
