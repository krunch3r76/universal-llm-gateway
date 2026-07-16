"""team_dispatch MCP relay to Stargate.

``team_dispatch(op=..., role=..., dispatch_thread_id=..., contract=..., ...)`` is
the sole agent-facing dispatch door. ``role`` selects a functional seat or API
role; each resolves its default (family, platform, model) via the ``role:{slug}``
execution contract in Cortex. Optional ``model=`` overrides within
``allowed_models``. Rosters are op-scoped (regenerate via
``scripts/gen-mcp-dispatch-role-docs`` — do not hand-edit the two lines below):

  generate/to_thread roles: reviewer, synthesizer, artisan, skeptic; auto seats: cursor-sdk
  handoff roles: web-consult, web-implement, cursor-consult, cursor-implement

Op enum: "generate" (auto result thread; on-behalf delivery), "to_thread" (reply
lands on ``thread`` via on-behalf delivery), or "handoff" (manual-seat agent-bus
thread via ``role=``).

Thin async-by-default relay: forward to Stargate, return the dispatch envelope
(execution_id, pipeline, started_at, status) immediately.

Callers:
- For ``op="generate"``: Stargate auto-provisions a result thread and posts the
  model's reply on the role's behalf; poll via ``agent_bus(tool="wait", …)`` from
  ``poll_hint`` (``pipeline(op="result")`` is metadata fallback). Do NOT instruct
  the model to "reply on this thread" — with ``mcp=true`` it self-posts on top of
  on-behalf delivery (friction #17396).
- For ``op="to_thread"``: Stargate posts the model's reply on the role's behalf
  when the dispatch completes. Read with
  ``agent_bus(tool="fetch", arguments={"thread": ...})``.
- For ``op="handoff"``: poll via ``agent_bus(tool="wait", …)`` from ``poll_hint``.

Persona-free ``/api/v1/frontier/dispatch`` remains on Stargate for internal
pipeline composition only — not exposed as an MCP tool.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal, get_args

import httpx
from mcp_events import record
from transport_utils import DEFAULT_STARGATE_URL, make_async_client
from universal_logging import get_logger

from ._restart_probe import annotate_unreachable_error

from ._frontier_intake import (
    normalize_dispatch_model,
    reject_pointer_body_on_generate,
    reject_unsupported_packet_inputs,
    require_dispatch_thread_id,
    require_explicit_cursor_seat_for_handoff,
    validate_inline_prompt_inputs,
    validate_wrap_inputs,
)

if TYPE_CHECKING:
    from fastmcp import FastMCP

logger = get_logger(__name__)

# Gen-gated roster constants — regenerate via scripts/gen-mcp-dispatch-role-docs;
# do not hand-edit the two lines below.
_HANDOFF_ROLE_ROSTER = "web-consult, web-implement, cursor-consult, cursor-implement"
_HANDOFF_SEAT_ROSTER = "web-anthropic, cursor"

# Relay only handles admission (role contract + model admission at Stargate).
# Long-poll blocking is the caller's responsibility via pipeline(op="result").
_RELAY_TIMEOUT = 20.0


# --- density_triage cross-process drift guard (spec A1/A3; thread 3642 arc) ---
# This MCP tool surface advertises a density_triage Literal that MUST stay equal to
# the canonical accepted set in config/mcp/canonical.yaml. frontier.py cannot import
# Stargate, so it reads the canonical config directly. Enforced loudly at import;
# goes live at the next operator-approved MCP rebuild.
_DENSITY_TRIAGE_LITERAL = Literal[
    "mechanical",
    "judgment_required",
    "recon_pending",
    "cross_cutting",
    "dispatch_surface",
    "admission_path",
    "trivial",
]


def _assert_density_triage_canonical() -> None:
    """Fail loudly at import if the advertised density_triage Literal diverges from
    the canonical accepted set (or the canonical config is missing/malformed)."""
    from pathlib import Path

    import yaml  # local import: keep module import cheap and import-safe

    canonical_path = (
        Path(__file__).resolve().parents[3] / "config" / "mcp" / "canonical.yaml"
    )
    if not canonical_path.is_file():
        raise RuntimeError(
            "density_triage drift guard: canonical config not found at "
            f"{canonical_path}. config/mcp/canonical.yaml is the MCP tool-contract "
            "surface and must be present."
        )
    try:
        data = yaml.safe_load(canonical_path.read_text(encoding="utf-8")) or {}
    except Exception as exc:  # noqa: BLE001 - surface any parse failure loudly
        raise RuntimeError(
            f"density_triage drift guard: failed to parse {canonical_path}: {exc}"
        ) from exc
    try:
        accepted = data["contract_vocabulary"]["density_triage"]["accepted_values"]
    except (KeyError, TypeError) as exc:
        raise RuntimeError(
            "density_triage drift guard: key "
            "contract_vocabulary.density_triage.accepted_values missing from "
            f"{canonical_path}."
        ) from exc
    canonical_set = set(accepted)
    literal_set = set(get_args(_DENSITY_TRIAGE_LITERAL))
    if literal_set != canonical_set:
        raise RuntimeError(
            "density_triage drift guard: frontier.py Literal diverges from the "
            f"canonical set in {canonical_path}.\n"
            f"  frontier.py Literal : {sorted(literal_set)}\n"
            f"  canonical accepted  : {sorted(canonical_set)}\n"
            f"  only in Literal     : {sorted(literal_set - canonical_set)}\n"
            f"  only in canonical   : {sorted(canonical_set - literal_set)}"
        )


_assert_density_triage_canonical()
# --- end density_triage drift guard ---


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
            return annotate_unreachable_error(
                code="stargate_unreachable",
                message=str(exc),
                service="stargate",
            )

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
            violations = []
            for item in detail_obj:
                if not isinstance(item, dict):
                    continue
                loc = item.get("loc") or []
                field_i = str(loc[-1]) if loc else ""
                violations.append(
                    {
                        "field": field_i,
                        "message": item.get("msg") or "validation error",
                        "type": item.get("type") or "validation_error",
                    }
                )
            first = violations[0] if violations else {}
            field = str(first.get("field") or "")
            msg = str(first.get("message") or "validation error")
            record(
                f"{record_prefix}.rejected",
                status=resp.status_code,
                field=field,
            )
            return {
                "error": {"code": "validation_error", "message": msg},
                "field": field,
                "validation_errors": violations,
            }
        field = detail_obj.get("field", "") if isinstance(detail_obj, dict) else ""
        record(
            f"{record_prefix}.rejected",
            status=resp.status_code,
            field=field,
        )
        return detail_obj if isinstance(detail_obj, dict) else {"error": payload}

    if isinstance(payload, dict) and payload.get("status") == "queued":
        record(
            f"{record_prefix}.queued",
            execution_id=payload.get("execution_id", ""),
        )
        return payload

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
        dispatch_thread_id: str = "",
        model: str | None = None,
        mcp: bool | None = None,
        system: str = "",
        reasoning_effort: str | None = None,
        generation_options: dict[str, Any] | None = None,
        max_tool_turns: int | None = None,
        transcript_id: str | None = None,
        caller_agent: str | None = None,
        timeout_seconds: int | None = None,
        bus_lifecycle: Literal["persistent", "ephemeral"] | None = None,
        thread: str | None = None,
        subject: str | None = None,
        packet_path: str | None = None,
        source_ref: str | None = None,
        contract: Literal["light-bounded", "pure-mechanical", "implement", "wrap"]
        | None = None,
        density_triage: _DENSITY_TRIAGE_LITERAL | None = None,
        review_opt_out_reason_code: (
            Literal[
                "routine_single_subsystem",
                "suggestion_only_first_pass",
                "cost_exceeds_false_negative_risk",
            ]
            | None
        ) = None,
        auto_review_child: bool = False,
        model_knobs: dict[str, str] | None = None,
        reuse_thread: str | None = None,
        executor_override: str | None = None,
        executor_override_reason_code: str | None = None,
        executor_override_reason: str | None = None,
        pointer_body: str | None = None,
        prompt: str | None = None,
        sidecar_ref: str | None = None,
        tags: list[str] | None = None,
        skills: list[str] | None = None,
        server_tools: bool | None = None,
        cost_intent: Literal["deliberate_high_cost"] | None = None,
        suppress_cost_warning: bool = False,
        cost_intent_reason: str | None = None,
        spawn_review_provenance: Literal["generate_review_child"] | None = None,
    ) -> dict[str, Any]:
        """Team-seat dispatch with explicit op discrimination.

        **``op="handoff"``** — manual seats; select ``seat``:

        - ``seat="web-anthropic"`` → operator pushes bus message
        - ``seat="cursor"`` → open IDE thread
        - Legacy aliases ``claude-web`` / ``claude-cursor`` still resolve

        **Contract (authority grant) — handoff only:** for ``op="handoff"`` the
        ``contract`` param is OPTIONAL and, when supplied, is the
        highest-priority explicit override. When omitted, Stargate derives the
        handoff contract (handoff admission path only):
        explicit param → ``source_ref`` dispatch_lane → packet front-matter
        ``contract:`` → role ``default_contract`` → default ``consult``.
        The derived value may be ``consult`` — a handoff-only contract that is
        NOT a passable ``contract`` argument (the param enum is
        ``light-bounded | pure-mechanical | implement``) and is NOT a valid
        generate/to_thread contract. A packet with acceptance criteria in
        ``<task_guidance>`` but no contract signal resolves to
        ``handoff_contract_ambiguous`` (422) — never silent consult admission.
        Generate/to_thread contract rules are in the ``op="generate"`` /
        ``op="to_thread"`` section below.

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
        ``.cursor/skills/handoff-packet-authoring/SKILL.md``.

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

        generate/to_thread roles: reviewer, synthesizer, artisan, skeptic; auto seats: cursor-sdk

        exactly one of ``role`` or ``seat`` is required for generate/to_thread. Each role carries a default
        provider model used when ``model`` is omitted on those ops.

        **No role? Different tool.** A role-LESS direct model one-shot does
        not go through team_dispatch — it is first-class on the pipeline
        surface: ``pipeline(op="async", pipeline_id="chat-dispatch",
        pipeline_options={"model": ...}, messages=[...])`` (any frontier
        chat model via its native endpoint; renamed from
        ``frontier-dispatch``).

        **Contract (REQUIRED — generate/to_thread):** ``contract`` is REQUIRED
        on ``op="generate"`` and ``op="to_thread"``; there is NO derivation on
        these paths.         ``op="generate"`` accepts
        ``light-bounded | pure-mechanical | implement | wrap`` (``implement`` and
        ``wrap`` are generate-only — the ``cursor-sdk`` packet lane); ``contract=wrap`` is
        also generate-only on ``seat=cursor-sdk`` — server-side gate-then-
        materialize via ``prepare_implement_packet``, returns HTTP 200 with
        ``packet_path`` + provenance (no SDK worker); requires ``source_ref``,
        forbids ``packet_path``, exempts ``dispatch_thread_id``; rejects
        gating-misleading knobs (``density_triage``, ``review_opt_out_reason_code``,
        ``auto_review_child``). For ``seat=cursor-sdk`` generate,
        ``packet_path`` is honored across ``light-bounded``, ``pure-mechanical``,
        and ``implement``; ``source_ref`` is implement-only (and ``wrap``).
        When no ``packet_path`` is supplied, prompt context is read from the
        latest turn on ``dispatch_thread_id`` (bus-turn fallback). Prefer
        ``prompt=`` or ``sidecar_ref=`` (cortex:// or workspaces path) to
        supply the brief on the admit call so it cannot desync from the latest
        bus turn (friction 24391). Exactly one of ``packet_path``, ``prompt``,
        or ``sidecar_ref`` may be explicit; otherwise the role-gated latest bus
        turn is fallback. ``op="to_thread"`` accepts ``light-bounded |
        pure-mechanical``. An omitted ``contract`` is
        rejected with ``validation_error`` "contract is required for
        op='generate'/'to_thread'". The legacy ``consult`` contract is DROPPED
        (operator ruling 2026-06-12, ``decision:team-dispatch-messages-fold``) —
        it is NOT aliased to ``light-bounded``; migrate explicitly.

        Three ops:
        - ``op="generate"``: admits dispatch and returns ``{execution_id,
          capabilities, knob_resolution, ...}``. ``capabilities`` echoes
          effective ``inline_only``, ``mcp_connector_active``, ``tool_surface``,
          and ``resolved_model`` for the admitted role. ``mcp_connector_active``
          is True iff Stargate activated a provider-side MCP connector for this
          dispatch; it is not a general indicator of executor tool access — use
          ``tool_surface`` for that. Returns ``knob_resolution`` for reasoning
          knob transparency: ``value_kind``, ``reasoning_native``, ``status``,
          ``parity`` (``not_claimed`` unless otherwise stated), and ``notes``.
          For API roles, ``op="generate"`` defaults to single-thread Q/R: when
          ``dispatch_thread_id`` is a numeric open thread already carrying the
          prompt (``turn_count>=1``), the pointer and the on-behalf reply land on
          that same thread and ``poll_hint`` targets it with the correct
          ``after_turn``; a fresh result thread is minted only when the dispatch
          thread is not reusable or ``split_thread=true`` is passed;
          ``reuse_thread`` still overrides explicitly. Stargate posts the role
          reply on its behalf (system-on-behalf delivery); poll via
          ``agent_bus(tool="wait", ...)`` from ``poll_hint``
          (``pipeline(op="result")`` is metadata fallback). The
          Without an explicit ``prompt`` / ``sidecar_ref`` / ``packet_path``,
          the role-gated ``dispatch_thread_id`` latest turn becomes the prompt:
          do NOT instruct the model to "reply on this thread" — with
          ``mcp=true`` that triggers a redundant model self-post on top of
          on-behalf delivery (friction #17396).
          If reasoning effort matters, inspect ``knob_resolution.status/parity/notes``;
          do not infer cross-provider parity.
          ``thread`` must be absent when using this op. For cursor-sdk,
          ``reuse_thread`` reuses a ``create_thread(lifecycle_state=pending)``
          shell so dispatch pointer and closeout land on one thread;
          ``dispatch_thread_id`` stays the arc coordination thread when it
          differs. ``subject`` is accepted but IGNORED — it is not
          persisted (the result-thread subject is auto-derived); the response
          carries a ``subject_ignored_on_generate`` warning. Use ``op="to_thread"``
          to actually set a thread subject (friction 19803).
          ``pointer_body`` is handoff-only and rejects with a validation
          error on ``op="generate"``/``op="to_thread"``. Use ``prompt`` or
          ``sidecar_ref`` for atomic brief+admit; the dispatch-thread latest
          turn is fallback only (friction 23301; previously silently dropped).
          Manual seats (``claude-web``, ``claude-cursor``) are rejected with 422
          ``web_seat_not_generate_target``. The SDK auto seat ``cursor-sdk`` (``seat=``) is
          admitted on ``op=generate`` (``auto_dispatchable`` substrate=sdk). Use API roles with optional
          ``model=`` override for provider-specific consults. Check/review default remains
          ``openai/gpt-5.6-terra`` (``check_review_default_model``); supported cursor-sdk
          option for the same work is ``seat=cursor-sdk`` + ``model=cursor/gpt-5.6-*`` or
          ``cursor/grok-4.5`` — poll ``reply_from_agent`` (reviewer/skeptic), not ``cursor-sdk``.
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

        Tool surface (defaults derived from the effective model; ``mcp`` overrides
        MCP-class tools only — client-side loop and remote connector, not
        provider server-side built-ins):
        - xAI multi-agent models — no client-side MCP tools.
        - Anthropic models — remote MCP connector by default when MCP-class tools
          are enabled; pass ``mcp=False`` for a one-shot inline generation (no
          client-side loop and no remote MCP connector).
        - Other MCP-capable providers — in-process tool loop unless ``mcp=False``.
        - ``mcp``: ``None`` (default) keeps the per-model default (tools-on for
          tool-capable families); ``False`` forces inline-only for MCP-class tools;
          inline-only families (e.g. gemini) stay clamped to no-tools regardless.
        - ``server_tools``: ``bool|None`` — omit for default ALL card-derived
          provider built-ins; ``False`` suppresses card-derived built-ins.
          Provider-neutral no-op where a provider has none (OQ-b ruling).
        - ``skills``: unified skills input path; capability-selected delivery;
          ``list[str]``; unsupported on ``op="handoff"`` (see validation above).
          MCP-predicated skills on a non-MCP dispatch reject 422
          ``skills_mcp_predicated`` naming the offenders; scope-default
          predicated skills are skipped with an event, never rejected.

        ``dispatch_thread_id`` — required compaction key and caller-owned
        thread persistence on the ``team-dispatch`` pipeline (generate/to_thread
        only) — **required** and validated at intake: an empty value is
        rejected with a descriptive 422 naming the field rather than failing
        late in the pipeline. **Exempt for ``contract=wrap``** on
        ``op=generate`` — wrap materializes from ``source_ref`` only and never
        reads the dispatch thread. Prompt context for other generate/to_thread
        contracts without ``packet_path`` is read from the latest turn body on
        this agent-bus thread; for ``seat=cursor-sdk`` generate with
        ``packet_path``, the packet is the instruction channel (bus turn
        ignored when both are present). ``messages[]`` is not a
        team_dispatch parameter. Distinct from ``thread`` (agent-bus
        delivery on ``op="to_thread"``) and ``transcript_id`` (provenance only).

        ``transcript_id`` — caller's session ID for provenance attribution only.
        It is recorded in the execution record alongside ``caller_agent`` so
        dispatches can be traced back to the originating session. It is NOT
        forwarded to the dispatched role's context — the receiving model never
        sees it.

        ``reasoning_effort`` — requested reasoning knob; actual resolution is
        reported in ``knob_resolution``. No parity claim by default. NOTE:
        ``reasoning_effort`` is NOT forwarded on ``seat="cursor-sdk"`` dispatches
        (it is dropped with a ``reasoning_effort_ignored`` warning) — use
        ``model_knobs`` instead.

        ``model_knobs`` — cursor-sdk model-variant knobs (``op="generate"``,
        ``seat="cursor-sdk"``) aligned against the resolved Cursor model's
        capability descriptor (``libs/cursor_capabilities``). E.g. on
        ``model="cursor/claude-opus-4-8"`` pass
        ``model_knobs={"effort": "low", "thinking": "false"}``. Unsupported or
        invalid knob values are dropped with a ``knob_dropped`` warning; the
        per-knob outcome is echoed in ``knob_resolution``. Ignored on API roles.
        """
        prompt_input_err = validate_inline_prompt_inputs(
            op, contract, packet_path, source_ref, prompt, sidecar_ref
        )
        if prompt_input_err is not None:
            return prompt_input_err

        if op == "handoff":
            if contract == "wrap":
                return {
                    "error": {
                        "code": "validation_error",
                        "message": (
                            "contract=wrap is only valid with op='generate', "
                            "seat='cursor-sdk'"
                        ),
                    },
                    "field": "contract",
                }
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
            _cursor_seat_err = require_explicit_cursor_seat_for_handoff(
                op=op, seat=seat, role=role
            )
            if _cursor_seat_err is not None:
                return _cursor_seat_err
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
            if skills is not None:
                return {
                    "error": {
                        "code": "validation_error",
                        "message": "skills is not supported when op='handoff'",
                    },
                    "field": "skills",
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
                ("bus_lifecycle", bus_lifecycle),
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

        if not role and not seat:
            return {
                "error": {
                    "code": "validation_error",
                    "message": (
                        "exactly one of role or seat is required when "
                        "op='generate' or op='to_thread'"
                    ),
                },
                "field": "role",
            }
        if role and seat:
            return {
                "error": {
                    "code": "validation_error",
                    "message": (
                        "role and seat are mutually exclusive when "
                        "op='generate' or op='to_thread'"
                    ),
                },
                "field": "role",
            }
        if role == "cursor-sdk":
            return {
                "error": {
                    "code": "role_is_not_a_seat",
                    "message": (
                        "'cursor-sdk' names an executor seat (platform=sdk), "
                        'not a functional role. Use seat="cursor-sdk".'
                    ),
                },
                "field": "role",
            }

        pointer_body_err = reject_pointer_body_on_generate(op, pointer_body)
        if pointer_body_err is not None:
            return pointer_body_err

        # Intake normalization + validation (F16655/F16656/F16657) — see
        # tools/_frontier_intake.py. Each guard returns an error envelope the
        # caller surfaces verbatim; the model strip is applied before forwarding.
        model = normalize_dispatch_model(model)

        body: dict[str, Any] = {
            "op": op,
            "dispatch_thread_id": dispatch_thread_id,
            "system": system,
        }
        if role is not None:
            body["role"] = role
        if seat is not None:
            body["seat"] = seat
        if contract is None:
            return {
                "error": {
                    "code": "validation_error",
                    "message": (
                        "contract is required for op='generate'/'to_thread'; "
                        "use light-bounded, pure-mechanical, implement, or wrap"
                    ),
                },
                "field": "contract",
            }
        seat_is_sdk = seat == "cursor-sdk"
        wrap_err = validate_wrap_inputs(
            op,
            contract,
            seat_is_sdk or role == "cursor-sdk",
            packet_path,
            source_ref,
            density_triage=density_triage,
            review_opt_out_reason_code=review_opt_out_reason_code,
            auto_review_child=auto_review_child,
        )
        if wrap_err is not None:
            return wrap_err
        packet_input_err = reject_unsupported_packet_inputs(
            op, contract, packet_path, source_ref
        )
        if packet_input_err is not None:
            return packet_input_err
        thread_id_err = require_dispatch_thread_id(op, dispatch_thread_id, contract)
        if thread_id_err is not None:
            return thread_id_err
        subject_ignored_on_generate = False
        if op == "generate":
            if thread is not None:
                return {
                    "error": {
                        "code": "validation_error",
                        "message": (
                            "thread is not allowed when op='generate' "
                            "(generate auto-provisions its own result thread)"
                        ),
                    }
                }
            # `subject` is harmless filler on generate: the result-thread subject
            # is auto-derived server-side (api_role_generate.py:
            # f"{role} generate — {request_id}"), so a caller-supplied subject
            # cannot be persisted here. Rather than hard-422 a readability label
            # (friction 19803), accept it, drop it from the forwarded body, and
            # surface a non-fatal warning on the response envelope. `thread`
            # stays rejected because it IS structurally invalid for generate.
            if subject is not None:
                subject_ignored_on_generate = True
            # cursor-sdk implement path: forward packet_path + contract so the
            # Stargate generate intercept (route.py) can route to the worker.
            # source_ref is ALSO forwarded — the first-class wrap transport
            # (todo:first-class-wrap-transport) added source_ref to
            # TeamDispatchGenerateBody so a bare source_ref (no packet_path)
            # materializes the implement packet server-side via
            # resolve_source_ref_to_packet. None-guarded, so API roles unaffected.
            if packet_path is not None:
                body["packet_path"] = packet_path
            if source_ref is not None:
                body["source_ref"] = source_ref
            if prompt is not None:
                body["prompt"] = prompt
            if sidecar_ref is not None:
                body["sidecar_ref"] = sidecar_ref
            if contract is not None:
                body["contract"] = contract
            if density_triage is not None:
                body["density_triage"] = density_triage
            if review_opt_out_reason_code is not None:
                body["review_opt_out_reason_code"] = review_opt_out_reason_code
            if auto_review_child:
                body["auto_review_child"] = auto_review_child
            if spawn_review_provenance is not None:
                body["spawn_review_provenance"] = spawn_review_provenance
            if reuse_thread is not None:
                body["reuse_thread"] = reuse_thread
        else:
            if contract in ("implement", "wrap"):
                return {
                    "error": {
                        "code": "validation_error",
                        "message": (
                            f"contract={contract} is only valid with "
                            "op='generate', seat='cursor-sdk'"
                        ),
                    },
                    "field": "contract",
                }
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
            if prompt is not None:
                body["prompt"] = prompt
            if sidecar_ref is not None:
                body["sidecar_ref"] = sidecar_ref
            body["contract"] = contract
            if auto_review_child:
                body["auto_review_child"] = auto_review_child
            if spawn_review_provenance is not None:
                body["spawn_review_provenance"] = spawn_review_provenance

        for key, val in (
            ("model", model),
            ("mcp", mcp),
            ("skills", skills),
            ("server_tools", server_tools),
            ("reasoning_effort", reasoning_effort),
            ("generation_options", generation_options),
            ("model_knobs", model_knobs),
            ("max_tool_turns", max_tool_turns),
            ("transcript_id", transcript_id),
            ("caller_agent", caller_agent),
            ("timeout_seconds", timeout_seconds),
            ("bus_lifecycle", bus_lifecycle),
            ("cost_intent", cost_intent),
            ("cost_intent_reason", cost_intent_reason),
            ("suppress_cost_warning", suppress_cost_warning if suppress_cost_warning else None),
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
        result = await _relay(
            endpoint="/api/v1/team/dispatch",
            body=body,
            record_prefix="mcp.team.dispatch",
        )
        if (
            subject_ignored_on_generate
            and isinstance(result, dict)
            and "error" not in result
        ):
            existing = result.get("warnings")
            warnings = list(existing) if isinstance(existing, list) else []
            warnings.append(
                "subject_ignored_on_generate: `subject` is not persisted on "
                "op='generate' (the result-thread subject is auto-derived). "
                "Drop it, or use op='to_thread' to set a thread subject."
            )
            result["warnings"] = warnings
        return result
