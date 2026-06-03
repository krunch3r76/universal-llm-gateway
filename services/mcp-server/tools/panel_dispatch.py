"""MCP tool — consensus panel helper (Phase 2, thread 1206).

Runs the default ≥2-provider panel (skeptic + reviewer; optional synthesizer
tiebreaker) via ``team_dispatch`` admission, returning execution IDs and family
labels for Menu D asserts. Invoke when ``consensus_disposition=panel``; lead
adjudication and cortex assert remain NON-offloadable (Guard 2).
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, Literal

from agent_seat.panel_dispatch import (
    PanelAdmissionPlan,
    admit_panel_plan,
    build_team_dispatch_body,
    effective_model_for_member,
    panel_result_envelope,
)
from mcp_events import record
from universal_logging import get_logger

if TYPE_CHECKING:
    from fastmcp import FastMCP

logger = get_logger(__name__)


async def _relay_team_dispatch(body: dict[str, Any]) -> dict[str, Any]:
    from tools.frontier import _relay

    return await _relay(
        endpoint="/api/v1/team/dispatch",
        body=body,
        record_prefix="mcp.panel.dispatch",
    )


def _poll_execution(execution_id: str, wait_seconds: float) -> dict[str, Any]:
    from tools.pipeline import _pipeline_result

    return _pipeline_result(execution_id, wait_seconds)


def register_panel_dispatch_tools(mcp: FastMCP) -> None:
    """Register ``panel_dispatch`` on the MCP server."""

    @mcp.tool(title="Panel Dispatch")
    async def panel_dispatch(
        messages: list[dict[str, Any]],
        dispatch_thread_id: str,
        disposition: Literal["panel"] = "panel",
        include_synthesizer: bool = False,
        poll: bool = False,
        wait_seconds: int = 60,
        caller_agent: str | None = None,
        system: str = "",
    ) -> dict[str, Any]:
        """Run the consensus-steelman panel member dispatches (Phase 2 helper).

        **When ``consensus_disposition=panel``** on a material decision: admits
        the default ≥2-family roster (``skeptic`` → grok/xai,
        ``reviewer`` → gpt-5.5/openai) via ``team_dispatch(op=generate)``.
        Optional ``include_synthesizer`` adds the gemini tiebreaker (inline-only).

        Returns ``panel_executions`` (role → ``execution_id``),
        ``panel_families``, and per-role ``dispatches``. Poll member content with
        ``pipeline(op="result", execution_id=...)`` unless ``poll=True``.

        **NON-offloadable (Guard 2):** steelman, falsifier adjudication, lead
        review of panelist writes, and the ``lead_adjudication_artifact`` — the
        lead must author those after this helper returns.

        **Assert template (Menu D, SPLIT storage):** ``cortex(tool="assert", ...)``
        for claim + ``evidence_uris``; then ``cortex(tool="entity_update",
        attributes=build_panel_assert_attributes(...))`` on the decision entity.
        Requires ``lead_adjudication_artifact``, ``decisive_falsifier``, and ≥2
        ``execution:`` evidence_uris. ``validate_panel_assert_attributes`` is
        helper-only (Guard 3) until session-close audit binding lands.

        Args:
            messages: Latest user turn(s) for each panel member (compaction key
                is ``dispatch_thread_id``).
            dispatch_thread_id: Server-owned thread persistence for team-dispatch.
            disposition: Must be ``panel`` (helper rejects other dispositions).
            include_synthesizer: Add synthesizer (gemini) as named tiebreaker.
            poll: When true, block-poll each ``execution_id`` via pipeline result.
            wait_seconds: Per-member poll wait (capped by pipeline tool).
            caller_agent: Provenance slug for Stargate execution records.
            system: Optional extra system prefix for all panel members.
        """
        admitted = admit_panel_plan(
            disposition=disposition,
            include_synthesizer=include_synthesizer,
        )
        if isinstance(admitted, dict):
            record("mcp.panel.dispatch.rejected", reason="admission")
            return admitted

        plan: PanelAdmissionPlan = admitted
        member_models = {
            spec.role: effective_model_for_member(spec) for spec in plan.members
        }

        record(
            "mcp.panel.dispatch.called",
            roles=",".join(member_models),
            families=",".join(
                {m.split("/")[0] for m in member_models.values() if "/" in m}
            ),
        )

        async def _one(
            spec_role: str, body: dict[str, Any]
        ) -> tuple[str, dict[str, Any]]:
            payload = await _relay_team_dispatch(body)
            return spec_role, payload

        bodies = [
            (
                spec.role,
                build_team_dispatch_body(
                    spec=spec,
                    messages=messages,
                    dispatch_thread_id=dispatch_thread_id,
                    caller_agent=caller_agent,
                    system=system,
                ),
            )
            for spec in plan.members
        ]
        pairs = await asyncio.gather(*[_one(role, body) for role, body in bodies])
        dispatches = dict(pairs)

        poll_results: dict[str, Any] | None = None
        if poll:
            poll_results = {}
            wait = float(max(0, min(wait_seconds, 60)))
            for role, payload in dispatches.items():
                eid = payload.get("execution_id") if isinstance(payload, dict) else None
                if not eid:
                    poll_results[role] = {
                        "error": "no execution_id",
                        "dispatch": payload,
                    }
                    continue
                poll_results[role] = await asyncio.to_thread(_poll_execution, eid, wait)

        record(
            "mcp.panel.dispatch.dispatched",
            execution_ids=",".join(
                str(p.get("execution_id", ""))
                for p in dispatches.values()
                if isinstance(p, dict)
            ),
        )
        return panel_result_envelope(
            plan=plan,
            dispatches=dispatches,
            member_models=member_models,
            poll_results=poll_results,
        )
