"""MCP tool — consensus panel helper (Phase 2, thread 1206).

Runs the default ≥2-provider panel (skeptic + reviewer; optional synthesizer
tiebreaker) via ``team_dispatch(op=generate)`` admission only — no ``to_thread`` or
``handoff`` fan-out (Guard 2: lead adjudication precedes bus delivery). Returns
execution IDs and family labels for Menu D asserts. Invoke when
``consensus_disposition=panel``; adjudication and cortex assert remain
NON-offloadable.
"""

from __future__ import annotations

import asyncio
import os
from typing import TYPE_CHECKING, Any, Literal

from agent_seat.panel_dispatch import (
    TIEBREAKER_ROLE,
    PanelAdmissionPlan,
    admit_panel_plan,
    build_panel_poll_summary,
    build_team_dispatch_body,
    effective_model_for_member,
    lint_panel_messages,
    member_dispatch_thread_id,
    panel_provider_families,
    panel_result_envelope,
)
from agent_seat.panel_idempotency import (
    build_panel_request_fingerprint,
    check_or_reserve,
    commit,
    release,
)
from agent_seat.panel_idempotency import (
    disabled as panel_idem_disabled,
)
from implement_admission.admission_read import read_packet
from implement_admission.source_ref import SourceRefError
from mcp_events import record
from transport_utils import DEFAULT_AGENT_BUS_URL, make_async_client
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


def _latest_user_text(messages: list[dict[str, Any]]) -> str:
    for message in reversed(messages):
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, str) and content.strip():
            return content.strip()
    return ""


async def _stage_panel_member_turn(
    *,
    thread_id: str,
    role: str,
    body: str,
    caller_agent: str | None,
) -> None:
    """Pre-stage the panel member prompt on its dispatch thread."""
    token = os.getenv("AGENT_BUS_TOKEN", "").strip()
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    async with make_async_client(DEFAULT_AGENT_BUS_URL, timeout=10.0) as client:
        create_payload = {
            "id": thread_id,
            "slug": thread_id,
            "summary": f"panel dispatch member {role}",
            "tags": ["type:panel-dispatch", f"agent:{role}"],
            "lifecycle_state": "pending",
        }
        created = await client.post("/threads", json=create_payload, headers=headers)
        if created.status_code not in (200, 201, 409):
            raise RuntimeError(
                f"failed to create panel dispatch thread {thread_id}: "
                f"{created.status_code} {created.text[:200]}"
            )
        turn_payload = {
            "thread": thread_id,
            "from": caller_agent or "panel_dispatch",
            "to": role,
            "subject": f"Panel member prompt — {role}",
            "body": body,
            "after_turn": 0,
            "allow_long_body": True,
        }
        posted = await client.post("/turns", json=turn_payload, headers=headers)
        if posted.status_code not in (200, 201):
            raise RuntimeError(
                f"failed to post panel dispatch turn {thread_id}: "
                f"{posted.status_code} {posted.text[:200]}"
            )


def _poll_execution(execution_id: str, wait_seconds: float) -> dict[str, Any]:
    from tools.pipeline import _pipeline_result

    return _pipeline_result(execution_id, wait_seconds)


async def _poll_dispatches(
    dispatches: dict[str, Any],
    wait_seconds: int,
) -> dict[str, Any]:
    """Block-poll each execution_id in *dispatches*."""
    poll_results: dict[str, Any] = {}
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
    return poll_results


def _apply_source_ref(
    messages: list[dict[str, Any]],
    source_ref: str,
) -> list[dict[str, Any]]:
    packet = read_packet(source_ref)
    prefix = (
        f"--- source_ref: {source_ref} ---\n{packet.text}\n--- end source_ref ---\n\n"
    )
    out = [dict(m) for m in messages]
    for message in out:
        if message.get("role") == "user" and isinstance(message.get("content"), str):
            message["content"] = prefix + message["content"]
            return out
    out.insert(0, {"role": "user", "content": prefix.strip()})
    return out


_SOURCE_REF_GUIDANCE = (
    "Packet content is inlined in the user message; do not re-read it via fs "
    "in your first member turn."
)


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
        reasoning_effort: str | None = None,
        generation_options: dict[str, Any] | None = None,
        max_tool_turns: int | None = None,
        transcript_id: str | None = None,
        timeout_seconds: int | None = None,
        source_ref: str | None = None,
        panel_request_id: str | None = None,
        member_models: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Run the consensus-steelman panel member dispatches (Phase 2 helper).

        **When ``consensus_disposition=panel``** on a material decision: admits
        the default ≥2-family roster (``skeptic`` → grok/xai,
        ``reviewer`` → cursor/gpt-5.6-terra on cursor-sdk) via
        ``team_dispatch(op=generate)``.
        Optional ``include_synthesizer`` adds the gemini tiebreaker (inline-only).

        Returns ``panel_executions`` (role → ``execution_id``),
        ``panel_capabilities`` (per-member ``inline_only`` /
        ``mcp_connector_active`` / ``tool_surface`` / ``resolved_model``;
        ``mcp_connector_active`` = Stargate connector activated for this member,
        not executor tool access), ``panel_families``, and per-role
        ``dispatches``. Returns ``member_knob_resolution`` per panel member for reasoning knob
        transparency: ``value_kind``, ``reasoning_native``, ``status``,
        ``parity`` (``not_claimed`` unless otherwise stated), and ``notes``.
        Poll member content with ``pipeline(op="result", execution_id=...)``
        unless ``poll=True``.

        **NON-offloadable (Guard 2):** steelman, falsifier adjudication, and the
        ``panel_adjudication_artifact`` are authored by the **adjudicating
        caller** (the seat that invoked this helper) after it returns. This
        "adjudicating lead" is the caller's adjudication role for THIS panel — it
        is distinct from the ``web-consult`` handoff role (``team_dispatch(
        op=handoff, role=web-consult)`` → claude-web). The adjudicating caller
        may be any seat.

        **Assert template (Menu D, assertion SOT):** pass
        ``attributes=build_panel_assert_attributes(...)`` directly to
        ``cortex(tool="assert", ...)`` alongside the claim and ``evidence_uris``.
        ``assertion.attributes`` is the source of truth — ``entity_update(
        attributes=...)`` is at most an optional derived read-cache, NEVER the
        primary write (consensus-steelman-posture §3.1). Requires
        ``panel_adjudication_artifact``, ``decisive_falsifier``, and ≥2
        ``execution:`` evidence_uris. ``validate_panel_assert_attributes`` is
        helper-only (Guard 3); session-close gate runs panel_disposition_incomplete on scoped entities.

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
            reasoning_effort: Requested reasoning knob; actual resolution is
                reported in ``member_knob_resolution``. No parity claim by default.
            generation_options: Provider generation params forwarded to every
                member identically. NOT a model-override channel — per-member
                model rebinding goes in ``member_models``.
            max_tool_turns: Tool-loop cap per member.
            transcript_id: Provenance-only session id per member dispatch.
            timeout_seconds: Pipeline wall-clock cap per member.
            source_ref: Workspaces-relative packet path or URI; read at admission
                and inlined into the first user message (no fs-read in member turn).
            panel_request_id: Opt-in idempotency key; same id + equivalent inputs
                within the dedupe window returns the prior envelope without a
                second paid member fan-out.
            member_models: Optional role → ``provider/model`` overrides for the
                fixed roster (e.g. ``{"skeptic": "xai/grok-4.5"}``). Honored by
                the ≥2-family gate and forwarded per member. Do NOT smuggle
                model overrides through ``generation_options`` — those are
                provider generation params and are invisible to family
                resolution (friction 23301).
        """
        if generation_options:
            roster_keys = sorted(
                {"skeptic", "reviewer", TIEBREAKER_ROLE} & set(generation_options)
            )
            if roster_keys:
                record("mcp.panel.dispatch.rejected", reason="options_role_keys")
                return {
                    "error": {
                        "code": "validation_error",
                        "message": (
                            f"generation_options contains roster role keys "
                            f"{roster_keys!r}; per-member model overrides go in "
                            "member_models (role → provider/model), not "
                            "generation_options (provider generation params)"
                        ),
                    },
                    "field": "generation_options",
                }
        admitted = admit_panel_plan(
            disposition=disposition,
            include_synthesizer=include_synthesizer,
            member_models=member_models,
        )
        if isinstance(admitted, dict):
            record("mcp.panel.dispatch.rejected", reason="admission")
            return admitted

        plan: PanelAdmissionPlan = admitted

        lint_err = lint_panel_messages(messages)
        if lint_err is not None:
            record("mcp.panel.dispatch.rejected", reason="block_content")
            return lint_err

        reserved = False
        if panel_request_id and not panel_idem_disabled():
            fingerprint = build_panel_request_fingerprint(
                messages=messages,
                dispatch_thread_id=dispatch_thread_id,
                disposition=disposition,
                include_synthesizer=include_synthesizer,
                system=system,
                source_ref=source_ref,
                reasoning_effort=reasoning_effort,
                generation_options=generation_options,
                max_tool_turns=max_tool_turns,
                timeout_seconds=timeout_seconds,
                member_models=member_models,
            )
            idem = check_or_reserve(panel_request_id, fingerprint)
            if idem.kind == "conflict":
                record(
                    "mcp.panel.dispatch.rejected",
                    reason="idempotency_conflict",
                )
                return {
                    "error": {
                        "code": "validation_error",
                        "message": (
                            f"panel_request_id {panel_request_id} reused with "
                            "non-equivalent inputs"
                        ),
                    }
                }
            if idem.kind == "in_flight":
                record(
                    "mcp.panel.dispatch.deduped",
                    panel_request_id=panel_request_id,
                    age_s=round(idem.age_s, 1),
                    repolled=False,
                )
                return {
                    "idempotency_hit": True,
                    "status": "in_flight",
                    "panel_request_id": panel_request_id,
                    "_note": (
                        "prior identical panel_dispatch is admitting; not "
                        "resubmitted — re-call shortly for the full envelope"
                    ),
                }
            if idem.kind == "hit":
                stored = dict(idem.envelope or {})
                stored["idempotency_hit"] = True
                stored["panel_request_id"] = panel_request_id
                if poll:
                    synthetic = {
                        role: {"execution_id": eid}
                        for role, eid in stored.get("panel_executions", {}).items()
                    }
                    poll_results = await _poll_dispatches(synthetic, wait_seconds)
                    poll_summary = build_panel_poll_summary(
                        dispatches=synthetic,
                        poll_results=poll_results,
                        polled=True,
                    )
                    stored.update(poll_summary)
                record(
                    "mcp.panel.dispatch.deduped",
                    panel_request_id=panel_request_id,
                    age_s=round(idem.age_s, 1),
                    repolled=bool(poll),
                )
                return stored
            reserved = True

        member_messages = list(messages)
        member_system = system
        if source_ref:
            try:
                member_messages = _apply_source_ref(member_messages, source_ref)
            except SourceRefError as exc:
                if reserved and panel_request_id:
                    release(panel_request_id)
                record("mcp.panel.dispatch.rejected", reason="source_ref")
                return {
                    "error": {
                        "code": "validation_error",
                        "message": str(exc),
                    }
                }
            member_system = (
                f"{member_system}\n\n{_SOURCE_REF_GUIDANCE}".strip()
                if member_system
                else _SOURCE_REF_GUIDANCE
            )

        member_models = {
            spec.role: effective_model_for_member(spec) for spec in plan.members
        }
        member_keys = {
            spec.role: member_dispatch_thread_id(dispatch_thread_id, spec.role)
            for spec in plan.members
        }
        member_prompt = _latest_user_text(member_messages)
        if not member_prompt:
            record("mcp.panel.dispatch.rejected", reason="empty_prompt")
            return {
                "error": {
                    "code": "validation_error",
                    "message": "panel_dispatch messages must include a non-empty user message",
                }
            }

        record(
            "mcp.panel.dispatch.called",
            roles=",".join(member_models),
            families=",".join(panel_provider_families(member_models)),
        )

        async def _one(
            spec_role: str, body: dict[str, Any]
        ) -> tuple[str, dict[str, Any]]:
            payload = await _relay_team_dispatch(body)
            return spec_role, payload

        bodies = []
        try:
            for spec in plan.members:
                key = member_keys[spec.role]
                await _stage_panel_member_turn(
                    thread_id=key,
                    role=spec.role,
                    body=member_prompt,
                    caller_agent=caller_agent,
                )
                bodies.append(
                    (
                        spec.role,
                        build_team_dispatch_body(
                            spec=spec,
                            dispatch_thread_id=key,
                            caller_agent=caller_agent,
                            system=member_system,
                            reasoning_effort=reasoning_effort,
                            generation_options=generation_options,
                            max_tool_turns=max_tool_turns,
                            transcript_id=transcript_id,
                            timeout_seconds=timeout_seconds,
                        ),
                    )
                )
        except RuntimeError as exc:
            if reserved and panel_request_id:
                release(panel_request_id)
            record("mcp.panel.dispatch.rejected", reason="stage_thread")
            return {"error": {"code": "panel_stage_failed", "message": str(exc)}}
        pairs = await asyncio.gather(*[_one(role, body) for role, body in bodies])
        dispatches = dict(pairs)

        for role, payload in dispatches.items():
            if isinstance(payload, dict) and payload.get("execution_id"):
                record(
                    "mcp.panel.member.admitted",
                    role=role,
                    model=member_models[role],
                    execution_id=str(payload["execution_id"]),
                    dispatch_key=member_keys[role],
                )
            elif isinstance(payload, dict) and "error" in payload:
                err = payload.get("error")
                reason = (
                    err.get("code", "dispatch_error")
                    if isinstance(err, dict)
                    else "dispatch_error"
                )
                record(
                    "mcp.panel.member.failed",
                    role=role,
                    reason=str(reason),
                    elapsed_s=0,
                )

        submission_plan = [
            {
                "role": role,
                "model": member_models[role],
                "execution_id": (
                    dispatches[role].get("execution_id")
                    if isinstance(dispatches[role], dict)
                    else None
                ),
                "dispatch_key": member_keys[role],
            }
            for role in member_models
        ]

        poll_results: dict[str, Any] | None = None
        if poll:
            poll_results = await _poll_dispatches(dispatches, wait_seconds)

        poll_summary = build_panel_poll_summary(
            dispatches=dispatches,
            poll_results=poll_results,
            polled=poll,
        )

        if poll and poll_summary.get("status") == "partial":
            record(
                "mcp.panel.partial",
                in_flight_count=len(poll_summary.get("in_flight_execution_ids", [])),
            )

        if poll and poll_results:
            for role, poll_result in poll_results.items():
                if poll_summary["member_status"].get(role) != "failed":
                    continue
                if not isinstance(poll_result, dict):
                    continue
                if poll_result.get("status") != "failed":
                    continue
                err = poll_result.get("error")
                reason = (
                    err.get("code", "pipeline_failed")
                    if isinstance(err, dict)
                    else "pipeline_failed"
                )
                result = poll_result.get("result")
                elapsed_s = (
                    result.get("duration_s", 0) if isinstance(result, dict) else 0
                )
                record(
                    "mcp.panel.member.failed",
                    role=role,
                    reason=str(reason),
                    elapsed_s=elapsed_s,
                )

        record(
            "mcp.panel.dispatch.dispatched",
            execution_ids=",".join(
                str(p.get("execution_id", ""))
                for p in dispatches.values()
                if isinstance(p, dict)
            ),
        )
        envelope = panel_result_envelope(
            plan=plan,
            dispatches=dispatches,
            member_models=member_models,
            poll_results=poll_results,
            submission_plan=submission_plan,
            poll_summary=poll_summary,
            reasoning_effort=reasoning_effort,
            requested_max_output=(
                (generation_options or {}).get("max_tokens")
                if isinstance((generation_options or {}).get("max_tokens"), int)
                else None
            ),
        )
        if panel_request_id:
            envelope["panel_request_id"] = panel_request_id
            envelope["idempotency_hit"] = False
        if reserved and panel_request_id:
            if envelope.get("panel_executions"):
                commit(panel_request_id, envelope)
            else:
                release(panel_request_id)
        return envelope
