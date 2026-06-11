"""team_dispatch MCP relay to Stargate.

``team_dispatch(op=..., role=..., messages=..., dispatch_thread_id=..., ...)`` is
the sole agent-facing dispatch door. ``role`` selects a functional seat or API
role; each resolves its default (family, platform, model) via the ``role:{slug}``
execution contract in Cortex. Optional ``model=`` overrides within
``allowed_models``. Rosters are op-scoped (regenerate via
``scripts/gen-mcp-dispatch-role-docs`` — do not hand-edit the two lines below):

  generate/to_thread roles: reviewer, gatherer, synthesizer, artisan, skeptic
  handoff roles: web-consult, web-implement, cursor-consult, cursor-implement

Op enum: "generate" (returns content via tracker), "to_thread" (reply lands on
``thread``), or "handoff" (manual-seat agent-bus thread via ``role=``).

Thin async-by-default relay: forward to Stargate, return the dispatch envelope
(execution_id, pipeline, started_at, status) immediately.

Callers:
- For ``op="generate"``: poll with ``pipeline(op="result", execution_id=...)``
  to retrieve content.
- For ``op="to_thread"``: Stargate posts the model's reply on the role's behalf
  when the dispatch completes. Read with
  ``agent_bus(tool="fetch", arguments={"thread": ...})``.
- For ``op="handoff"``: poll via ``agent_bus(tool="wait", …)`` from ``poll_hint``.

Persona-free ``/api/v1/frontier/dispatch`` remains on Stargate for internal
pipeline composition only — not exposed as an MCP tool.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

import httpx
from mcp_events import record
from transport_utils import DEFAULT_STARGATE_URL, make_async_client
from universal_logging import get_logger

from ._frontier_intake import (
    normalize_dispatch_model,
    require_dispatch_thread_id,
    validate_dispatch_messages,
)

if TYPE_CHECKING:
    from fastmcp import FastMCP

logger = get_logger(__name__)

# Gen-gated roster constants — regenerate via scripts/gen-mcp-dispatch-role-docs;
# do not hand-edit the two lines below.
_HANDOFF_ROLE_ROSTER = "web-consult, web-implement, cursor-consult, cursor-implement"
_HANDOFF_SEAT_ROSTER = "claude-web, claude-cursor"

# Relay only handles admission (role contract + model admission at Stargate).
# Long-poll blocking is the caller's responsibility via pipeline(op="result").
_RELAY_TIMEOUT = 20.0


async def _relay(
    *,
    endpoint: str,
    body: dict[str, Any],
    record_prefix: str,
) -> dict[str, Any]:
    """Forward body to a Stargate endpoint and normalize the response envelope.

    Shared by ``team_dispatch`` generate/to_thread paths.
    The ``record_prefix`` parameter routes telemetry rows to the per-tool signal
    namespace (``mcp.team.dispatch.*`` / ``mcp.team.handoff.*``).
    """
    async with make_async_client(
        DEFAULT_STARGATE_URL, timeout=_RELAY_TIMEOUT
    ) as client:
        try:
            resp = await client.post(endpoint, json=body)
        except httpx.RequestError as exc:
            logger.error("%s relay transport failure: %s", record_prefix, exc)
            record(f"{record_prefix}.failed", error="transport")
            return {
                "error": {
                    "code": "stargate_unreachable",
                    "message": str(exc),
                }
            }

    try:
        payload = resp.json()
    except ValueError as exc:
        logger.error(
            "%s relay returned non-JSON response: status=%s error=%s",
            record_prefix,
            resp.status_code,
            exc,
        )
        record(
            f"{record_prefix}.failed",
            error="non_json",
            status=resp.status_code,
        )
        return {
            "error": {
                "code": f"http_{resp.status_code}",
                "message": resp.text[:500],
            }
        }

    # 5xx: upstream/infrastructure failure — bucket as `.failed`, not `.rejected`.
    # Stargate's `_error_response` issues 503s with `{"error": {...}}` JSON for
    # `pipeline_dispatch_unavailable` etc.; classifying those as `.rejected`
    # would attribute infra outages to caller misuse on dashboards.
    if resp.status_code >= 500:
        record(
            f"{record_prefix}.failed",
            error="upstream",
            status=resp.status_code,
        )
        return payload if isinstance(payload, dict) else {"error": payload}

    if resp.status_code >= 400:
        if isinstance(payload, dict) and "error" in payload:
            record(
                f"{record_prefix}.rejected",
                status=resp.status_code,
                field=payload.get("field") or "",
            )
            return payload
        detail_obj = payload.get("detail") if isinstance(payload, dict) else None
        # FastAPI's `extra="forbid"` 422 emits `{"detail": [{loc, msg, type, ...}]}` —
        # `detail` is a LIST. Normalize to the `FrontierEndpointError.to_dict()`
        # envelope shape so callers parse one error format and `.rejected`
        # carries the offending field name (last element of `loc`).
        if isinstance(detail_obj, list) and detail_obj:
            first = detail_obj[0] if isinstance(detail_obj[0], dict) else {}
            loc = first.get("loc") or []
            field = str(loc[-1]) if loc else ""
            msg = first.get("msg") or "validation error"
            record(
                f"{record_prefix}.rejected",
                status=resp.status_code,
                field=field,
            )
            return {
                "error": {"code": "validation_error", "message": msg},
                "field": field,
            }
        field = detail_obj.get("field", "") if isinstance(detail_obj, dict) else ""
        record(
            f"{record_prefix}.rejected",
            status=resp.status_code,
            field=field,
        )
        return detail_obj if isinstance(detail_obj, dict) else {"error": payload}

    record(
        f"{record_prefix}.dispatched",
        execution_id=payload.get("execution_id", "")
        if isinstance(payload, dict)
        else "",
    )
    return payload


def register_frontier_tools(mcp: FastMCP) -> None:
    """Register the team_dispatch MCP tool."""

    @mcp.tool(title="Team Dispatch")
    async def team_dispatch(
        op: Literal["generate", "to_thread", "handoff"],
        role: str | None = None,
        seat: str | None = None,
        messages: list[dict[str, Any]] = [],  # noqa: B006
        dispatch_thread_id: str = "",
        model: str | None = None,
        system: str = "",
        reasoning_effort: str | None = None,
        generation_options: dict[str, Any] | None = None,
        max_tool_turns: int | None = None,
        transcript_id: str | None = None,
        caller_agent: str | None = None,
        timeout_seconds: int | None = None,
        thread: str | None = None,
        subject: str | None = None,
        packet_path: str | None = None,
        source_ref: str | None = None,
        contract: Literal["consult", "implement"] | None = None,
        executor_override: str | None = None,
        executor_override_reason_code: str | None = None,
        executor_override_reason: str | None = None,
        pointer_body: str | None = None,
        tags: list[str] | None = None,
    ) -> dict[str, Any]:
        """Team-seat dispatch with explicit op discrimination.

        **``op="handoff"``** — manual seats; select ``seat``:

        - ``seat="claude-web"`` → operator pushes bus message
        - ``seat="claude-cursor"`` → open IDE thread

        **Contract (authority grant):** pass ``contract="consult"`` or
        ``contract="implement"`` for an explicit authority grant — highest
        priority in server-side derivation. When omitted, contract is derived:
        explicit param → ``source_ref`` dispatch_lane → packet front-matter
        ``contract:`` → role ``default_contract`` → default ``consult``.
        A packet with acceptance criteria in ``<task_guidance>`` but no contract
        signal resolves to ``handoff_contract_ambiguous`` (422) — never silent
        consult admission.

        **``source_ref``** — scheme-prefixed entity/pointer ref only:
        ``todo:`` / ``plan:`` / ``plan_phase:`` / ``plan:{slug}/phase-N`` /
        ``agent-bus:`` / ``packet:``. Bare filesystem paths are rejected
        (``source_ref_unparseable``). **Filesystem packets go via
        ``packet_path``, not ``source_ref``.**

        **``packet_path``** — repo-relative path from the gateway checkout root
        (``/mnt/torus/projects/universal-llm-gateway``): e.g.
        ``tmp/reviews/my-packet.md``. Do **not** prefix with
        ``universal-llm-gateway/`` (that prefix is for ``fs(sandbox=workspaces)``).
        A leading ``universal-llm-gateway/`` is stripped before resolution.

        **Packet shape:** six required XML blocks — ``<scope>``,
        ``<invariants>``, ``<task_guidance>``, ``<corpus>``,
        ``<mcp_capabilities>`` (MCP seats), ``<output_format>``. Author per
        ``docs/agent-guides/skills/handoff-packet-authoring.md``.

        The ``{platform}-{contract}`` shorthand slugs remain accepted and encode
        (seat, contract) — roster in the module docstring above.

        Requires ``subject``, at least one of ``seat`` | ``role``, and at least
        one of ``packet_path`` | ``source_ref``. Returns
        ``{thread_id, resolved_model, to_agent, recommended_executor,
        recommended_review, …}``.

        **Executor override (implement only):** optional
        ``executor_override`` + ``executor_override_reason_code`` +
        ``executor_override_reason`` (request or packet front-matter). Silence
        → server default ``recommended_executor=composer``. Structured
        opt-out codes: ``pure_cortex_doc_edit`` (with ``web-inline``),
        ``capability_gap`` / ``protocol_heavy`` (non-Composer tier), or
        ``design_judgment_remaining`` (re-scope warning, coerced to composer).
        Advisory on manual seats; IDE picker binds the actual executor tier.

        **``op="generate"`` / ``op="to_thread"``** — API functional roles via
        ``role`` (regenerate roster via ``scripts/gen-mcp-dispatch-role-docs``):

        generate/to_thread roles: reviewer, gatherer, synthesizer, artisan, skeptic

        ``role`` is required for generate/to_thread. Each role carries a default
        provider model used when ``model`` is omitted on those ops.

        Three ops:
        - ``op="generate"``: admits dispatch and returns ``{execution_id,
          capabilities, knob_resolution, ...}``. ``capabilities`` echoes
          effective ``inline_only``, ``mcp_enabled``, ``tool_surface``, and
          ``resolved_model`` for the admitted role. Returns ``knob_resolution`` for reasoning
          knob transparency: ``value_kind``, ``reasoning_native``, ``status``,
          ``parity`` (``not_claimed`` unless otherwise stated), and ``notes``.
          Poll with ``pipeline(op="result", execution_id=...)`` for content.
          If reasoning effort matters, inspect ``knob_resolution.status/parity/notes``;
          do not infer cross-provider parity.
          ``thread`` / ``subject`` must be absent when using this op.
          Synthetic seat models (``claude-web``, ``claude-cursor``) are rejected
          with 422 ``web_seat_not_generate_target``. Use API roles with optional
          ``model=`` override for provider-specific consults.
        - ``op="to_thread"``: admits dispatch; Stargate posts the role's
          reply to ``thread`` on its behalf after the dispatch completes
          (system-on-behalf delivery). Tracker terminal status reflects
          the POST outcome. ``thread`` is required. ``subject`` is
          optional (defaults to ``"{role} reply — execution {short_id}"``).
        - ``op="handoff"``: ``seat`` selects the destination; contract is derived server-side (the shorthand slugs still encode (seat, contract)).
          Returns
          ``{thread_id, subject, to_agent, resolved_model, push_reminder,
          recommended_executor, recommended_executor_source,
          recommended_review, result_handle, handoff_status, poll_hint}``. Poll via
          ``agent_bus(tool="wait", …)`` from ``poll_hint`` — not
          ``pipeline(op="result")``.

        Tool surface (no caller knob — derived from the effective model):
        - xAI multi-agent models — no client-side MCP tools.
        - Anthropic models — remote MCP when enabled by the dispatcher.
        - Other MCP-capable providers — in-process tool loop.

        ``dispatch_thread_id`` — required compaction key for server-owned
        thread persistence on the ``team-dispatch`` pipeline (generate/to_thread
        only) — **required** and validated at intake: an empty value is
        rejected with a descriptive 422 naming the field rather than failing
        late in the pipeline. Prior turns are assembled from cortex; pass only
        the **latest**
        user message in ``messages``. Distinct from ``thread`` (agent-bus
        delivery on ``op="to_thread"``) and ``transcript_id`` (provenance only).

        ``transcript_id`` — caller's session ID for provenance attribution only.
        It is recorded in the execution record alongside ``caller_agent`` so
        dispatches can be traced back to the originating session. It is NOT
        forwarded to the dispatched role's context — the receiving model never
        sees it.

        ``reasoning_effort`` — requested reasoning knob; actual resolution is
        reported in ``knob_resolution``. No parity claim by default.
        """
        if op == "handoff":
            if not subject:
                return {
                    "error": {
                        "code": "validation_error",
                        "message": "subject is required when op='handoff'",
                    }
                }
            if not packet_path and not source_ref:
                return {
                    "error": {
                        "code": "validation_error",
                        "message": (
                            "at least one of packet_path or source_ref is required "
                            "when op='handoff'"
                        ),
                    }
                }
            if not seat and not role:
                return {
                    "error": {
                        "code": "validation_error",
                        "message": (
                            f"at least one of seat ({_HANDOFF_SEAT_ROSTER}) or "
                            f"role ({_HANDOFF_ROLE_ROSTER}) is required "
                            "when op='handoff'"
                        ),
                    }
                }
            if model is not None:
                return {
                    "error": {
                        "code": "validation_error",
                        "message": (
                            f"model is not accepted when op='handoff'; select "
                            f"seat ({_HANDOFF_SEAT_ROSTER}) or shorthand role "
                            f"({_HANDOFF_ROLE_ROSTER})"
                        ),
                    }
                }
            handoff_body: dict[str, Any] = {
                "op": "handoff",
                "subject": subject,
            }
            if seat is not None:
                handoff_body["seat"] = seat
            if role is not None:
                handoff_body["role"] = role
            if packet_path is not None:
                handoff_body["packet_path"] = packet_path
            if source_ref is not None:
                handoff_body["source_ref"] = source_ref
            if contract is not None:
                handoff_body["contract"] = contract
            for key, val in (
                ("executor_override", executor_override),
                ("executor_override_reason_code", executor_override_reason_code),
                ("executor_override_reason", executor_override_reason),
                ("pointer_body", pointer_body),
                ("tags", tags),
                ("caller_agent", caller_agent),
            ):
                if val is not None:
                    handoff_body[key] = val
            record(
                "mcp.team.handoff.called",
                role=role or "",
                seat=seat or "",
                model="",
                to_agent="",
            )
            return await _relay(
                endpoint="/api/v1/team/handoff",
                body=handoff_body,
                record_prefix="mcp.team.handoff",
            )

        if not role:
            return {
                "error": {
                    "code": "validation_error",
                    "message": "role is required when op='generate' or op='to_thread'",
                }
            }

        # Intake normalization + validation (F16655/F16656/F16657) — see
        # tools/_frontier_intake.py. Each guard returns an error envelope the
        # caller surfaces verbatim; the model strip is applied before forwarding.
        thread_id_err = require_dispatch_thread_id(op, dispatch_thread_id)
        if thread_id_err is not None:
            return thread_id_err
        messages_err = validate_dispatch_messages(messages)
        if messages_err is not None:
            return messages_err
        model = normalize_dispatch_model(model)

        body: dict[str, Any] = {
            "op": op,
            "messages": messages,
            "role": role,
            "dispatch_thread_id": dispatch_thread_id,
            "system": system,
        }
        if op == "generate":
            if thread is not None or subject is not None:
                return {
                    "error": {
                        "code": "validation_error",
                        "message": "thread/subject are not allowed when op='generate'",
                    }
                }
        else:
            if thread is None:
                return {
                    "error": {
                        "code": "validation_error",
                        "message": "thread is required when op='to_thread'",
                    }
                }
            body["thread"] = thread
            if subject is not None:
                body["subject"] = subject

        for key, val in (
            ("model", model),
            ("reasoning_effort", reasoning_effort),
            ("generation_options", generation_options),
            ("max_tool_turns", max_tool_turns),
            ("transcript_id", transcript_id),
            ("caller_agent", caller_agent),
            ("timeout_seconds", timeout_seconds),
        ):
            if val is not None:
                body[key] = val

        record(
            "mcp.team.dispatch.called",
            role=role,
            op=op,
            model=model or "",
            reasoning_effort=reasoning_effort or "",
        )
        return await _relay(
            endpoint="/api/v1/team/dispatch",
            body=body,
            record_prefix="mcp.team.dispatch",
        )
