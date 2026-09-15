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

from typing import TYPE_CHECKING, Annotated, Any, Literal, get_args

import httpx
from mcp_events import record
from pydantic import Field
from team_dispatch_vocab import TeamDispatchContract
from transport_utils import DEFAULT_STARGATE_URL, make_async_client
from universal_logging import get_logger

from ._dispatch_caller_agent import infer_caller_agent_for_conductor
from ._frontier_intake import (
    normalize_dispatch_model,
    reject_pointer_body_on_generate,
    reject_unsupported_packet_inputs,
    require_cursor_sdk_checkout_lane,
    require_dispatch_thread_id,
    require_explicit_cursor_seat_for_handoff,
    validate_force,
    validate_inline_prompt_inputs,
    validate_work_key,
    validate_wrap_inputs,
)
from ._restart_probe import annotate_unreachable_error

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
        op: Literal["generate", "to_thread", "handoff", "steer"],
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
        contract: TeamDispatchContract | None = None,
        density_triage: _DENSITY_TRIAGE_LITERAL | None = None,
        review_opt_out_reason_code: (
            Literal[
                "routine_single_subsystem",
                "suggestion_only_first_pass",
                "cost_exceeds_false_negative_risk",
            ]
            | None
        ) = None,
        auto_review_child: bool | None = None,
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
        nest_under: Annotated[
            str | None,
            Field(
                description=(
                    "Parent cursor-sdk dispatch_id to park under for product-path "
                    "nesting (LIFO park stack, hard max depth 10; 11th nest → 422 "
                    "CURSOR_NEST_DEPTH_EXCEEDED). cursor-sdk-seat-only: valid only "
                    "for seat='cursor-sdk' generate/to_thread; other seats → 422 "
                    "nest_under_sdk_only."
                ),
            ),
        ] = None,
        resume_of: Annotated[
            str | None,
            Field(
                description=(
                    "Terminal parent cursor-sdk dispatch_id for SDK agent-plane "
                    "resume (same agent continues via resume_agent). Requires "
                    "reuse_thread=<parent worker thread>. XOR nest_under. "
                    "cursor-sdk-seat-only; other seats → 422 resume_of_sdk_only."
                ),
            ),
        ] = None,
        lane: Annotated[
            Literal["A", "B"] | None,
            Field(
                description=(
                    "Required GIW checkout-isolation lane ('A' | 'B') on "
                    "top-level seat='cursor-sdk' generate/to_thread. Distinct "
                    "from dispatch_lane (path-sim routing). Other seats → 422 "
                    "lane_sdk_only. Omit only when nest_under or resume_of "
                    "inherits parent isolation; otherwise 422 lane_required. "
                    "contract=wrap is exempt. See agent_skill:consult-routing."
                ),
            ),
        ] = None,
        workspace: Annotated[
            str | None,
            Field(
                description=(
                    "Allowlisted satellite repo name for per-dispatch git "
                    "identity (capture, land lease, head_sha). Omit for hub "
                    "ULG; control-plane stays on hub. cursor-sdk-seat-only; "
                    "other seats → 422 workspace_sdk_only."
                ),
            ),
        ] = None,
        purpose: Annotated[
            str | None,
            Field(
                description=(
                    "CDP registry/mission purpose tag on model=cdp/… generate "
                    "(default ask). Set purpose=operator-proxy or mission for "
                    "operator-proxy skill-chip inject; ignored on non-CDP models."
                ),
            ),
        ] = None,
        parent_thread: Annotated[
            str | None,
            Field(
                description=(
                    "Bus private-request parent lane (hop/side parent) on "
                    "model=cdp/… generate. Binds cdp_ask per-lane seat_cap "
                    "admission; distinct from SDK nest_under."
                ),
            ),
        ] = None,
        work_key: Annotated[
            str | None,
            Field(
                description=(
                    "Stable work identity for cursor-sdk admits (D4 grammar: "
                    "todo:, plan:, agent-bus:, packet:, friction:, decision:). "
                    "Required for write-class / Lane B; allowed on every contract "
                    "including none."
                ),
            ),
        ] = None,
        force: Annotated[
            bool,
            Field(
                description=(
                    "Bypass Gates 2–4 (in-flight, branch debt, remint cap) when "
                    "true; requires force_reason. Never bypasses Gate 1 or "
                    "write-lease FIFO."
                ),
            ),
        ] = False,
        force_reason: Annotated[
            str | None,
            Field(
                description=(
                    "Audit reason when force=true (e.g. fanout:composer-ab). "
                    "Required with force."
                ),
            ),
        ] = None,
        dispatch_id: Annotated[
            str | None,
            Field(
                description=(
                    "Target cursor-sdk dispatch_id when op='steer' "
                    "(park_for_restart, cancel_discard, or inject relay to GIW)."
                ),
            ),
        ] = None,
        steer: Annotated[
            Literal["park_for_restart", "cancel_discard", "inject"] | None,
            Field(
                description=(
                    "Steer verb when op='steer'. park_for_restart parks one live "
                    "dispatch for resume; cancel_discard kills without resume; "
                    "inject deposits a mid-hop directive without cancel."
                ),
            ),
        ] = None,
        reason: Annotated[
            str | None,
            Field(
                description=(
                    "Human-readable steer reason when op='steer' (required for "
                    "park_for_restart and inject)."
                ),
            ),
        ] = None,
        directive: Annotated[
            str | None,
            Field(
                description=(
                    "Steer directive text when op='steer' and steer='inject' "
                    "(required for inject)."
                ),
            ),
        ] = None,
        ttl_s: Annotated[
            int | None,
            Field(
                description=(
                    "Optional spool TTL seconds when steer='inject' (default 300)."
                ),
            ),
        ] = None,
    ) -> dict[str, Any]:
        """Team-seat dispatch — sole MCP dispatch door to Stargate. Async relay; returns `{execution_id, pipeline, started_at, status}`. Poll via `agent_bus(tool="wait", …)` from `poll_hint` (`pipeline(op="result")` metadata fallback only). Role-less one-shots: `pipeline(op="async", pipeline_id="chat-dispatch", …)` — ¬this tool.

**Ops:** `generate` | `to_thread` | `handoff` | `steer`.

**Global (generate/to_thread):** exactly one of `role`|`seat` · `contract` REQUIRED (no derivation) · `dispatch_thread_id` required (exempt `contract=wrap`) · at most one explicit of `packet_path`|`prompt`|`sidecar_ref` (else latest gated bus turn on `dispatch_thread_id`) · `messages[]` ¬a param · `transcript_id` provenance-only (¬forwarded to role).

---

**`op=steer`:** requires `dispatch_id`, `steer`∈{`park_for_restart`,`cancel_discard`,`inject`}, `reason`; `directive` required iff `steer=inject`; optional `ttl_s` (inject default **300**). Propagates GIW 404/409/422/503 fail-closed. `inject`→202 pending (no `park_kind`); `park_for_restart` ¬`poll_hint` — inherited `execution_id` in-flight until resume child CLOSEOUT; `cancel_discard` terminates link (terminal `poll_hint`).

**`op=handoff`:** manual seats — `seat`∈{`web-anthropic`,`cursor`} (legacy `claude-web`|`claude-cursor`). Requires `subject` + (`seat`|`role`) + (`packet_path`|`source_ref`). `contract` optional → derived: param → `source_ref` dispatch_lane → packet `contract:` → role `default_contract` → `consult`. Derived `consult` is handoff-only — ¬passable `contract` param (param enum `none`|`pure-mechanical`|`implement`). Packet AC without contract signal → **422 `handoff_contract_ambiguous`**. `packet_path` = repo-relative from checkout root (strip leading `universal-llm-gateway/`). `source_ref` schemes: `todo:`|`plan:`|`plan_phase:`|`plan:{slug}/phase-N`|`agent-bus:`|`packet:` — bare FS paths → **`source_ref_unparseable`**; filesystem packets via `packet_path` only. Six-block packet shape — See `agent_skill:handoff-packet-authoring`. `pointer_body` handoff-only. Poll `agent_bus(wait)`. `executor_override` (+codes/reason) implement-only advisory on manual seats.

**`op=generate`:** `contract`∈{`none`,`pure-mechanical`,`implement`,`wrap`} (`implement`|`wrap` generate-only). `split_thread=true` or non-reusable dispatch thread → fresh result thread; `reuse_thread` overrides explicitly. `role=reviewer` omit-model → coerced `seat=cursor-sdk` (check_review default from `route_policy.yaml`). `contract=wrap` (cursor-sdk): `source_ref` required, `packet_path` forbidden, `dispatch_thread_id` exempt; rejects `density_triage`|`review_opt_out_reason_code`|`auto_review_child`; HTTP 200 + `packet_path` + provenance, no SDK worker. `seat=cursor-sdk`: `packet_path` honored on `none`|`pure-mechanical`|`implement`; `source_ref` on `implement`|`wrap` or `contract=conductor` — See `agent_skill:conductor`. `subject` accepted but **IGNORED** (warning `subject_ignored_on_generate`); use `to_thread` to set subject. `pointer_body` → validation_error. `thread` must be absent. Manual web seats → **422 `web_seat_not_generate_target`**. `seat=cursor-sdk` admitted on generate. API roles (regen `scripts/gen-mcp-dispatch-role-docs`): reviewer, synthesizer, artisan, skeptic; auto seat `cursor-sdk`. On-behalf delivery — ¬ instruct model to self-post on thread when `mcp=true`. Returns `{execution_id, capabilities, knob_resolution, …}`.

**`op=to_thread`:** `contract`∈{`none`,`pure-mechanical`}. `thread` required. `subject` optional (default `"{role} reply — execution {short_id}"`). On-behalf reply to `thread`.

Omitted `contract` on generate/to_thread → **`validation_error`**. Legacy `consult` contract **DROPPED** — ¬aliased to `none`.

---

**cursor-sdk gates (generate/to_thread):**

| param | rule |
|---|---|
| `nest_under` | sdk-only → **422 `nest_under_sdk_only`**; LIFO depth 10; 11th → **422 `CURSOR_NEST_DEPTH_EXCEEDED`** (`retryable=false`) |
| `resume_of` | sdk-only → **422 `resume_of_sdk_only`**; requires `reuse_thread=<parent worker thread>`; **XOR `nest_under`** |
| `lane` | sdk-only → **422 `lane_sdk_only`**; top-level `A`|`B` required unless `nest_under`|`resume_of` inherits; else **422 `lane_required`**; `contract=wrap` exempt; `lane=B` without worktree → **422 `CURSOR_LANE_B_WORKTREE_MISSING`** |
| `workspace` | sdk-only → **422 `workspace_sdk_only`**; allowlisted satellite; omit = hub |
| `reasoning_effort` | non-empty on cursor-sdk → **422 `reasoning_effort_not_supported`** (use `model_knobs`); empty → omit |
| `work_key` | D4 grammar `todo:`|`plan:`|`agent-bus:`|`packet:`|`friction:`|`decision:`; required write-class / Lane B |

**Skills (`generate`):** `list[str]`; rejected on handoff. cursor-sdk: unresolved slug → **422 `skills_cursor_unresolvable`**; MCP-predicated on non-MCP → **422 `skills_mcp_predicated`**; CDP `path-sim` → **422 `cdp_skills_path_sim_rejected`**. `purpose` on `model=cdp/…` default `ask`; `operator-proxy`|`mission` for chip inject — ignored non-CDP.

**Tool surface:** `mcp` None = per-model default; `False` = inline-only MCP-class. `server_tools` None = all card built-ins; `False` suppress (provider-neutral no-op). xAI: no client MCP. Anthropic: remote connector default when MCP on. `knob_resolution` reports reasoning knob outcome; no default parity claim.

**Other params:** `force=true` bypasses Gates 2–4 only; requires `force_reason`; never Gate 1 / write-lease FIFO. `density_triage`∈{`mechanical`,`judgment_required`,`recon_pending`,`cross_cutting`,`dispatch_surface`,`admission_path`,`trivial`}. `review_opt_out_reason_code`∈{`routine_single_subsystem`,`suggestion_only_first_pass`,`cost_exceeds_false_negative_risk`}. `spawn_review_provenance=generate_review_child`. `cost_intent=deliberate_high_cost`. `parent_thread` CDP per-lane cap (distinct from SDK `nest_under`). `dispatch_thread_id` distinct from `thread` (to_thread delivery) and `transcript_id`.

Depth: `agent_skill:dispatch-workflow` · `agent_skill:consult-routing` · `agent_skill:handoff-packet-authoring` · `agent_skill:conductor`.
        """
        prompt_input_err = validate_inline_prompt_inputs(
            op, contract, packet_path, source_ref, prompt, sidecar_ref
        )
        if prompt_input_err is not None:
            return prompt_input_err

        if op == "steer":
            if steer not in ("park_for_restart", "cancel_discard", "inject"):
                return {
                    "error": {
                        "code": "validation_error",
                        "message": (
                            "steer must be 'park_for_restart', 'cancel_discard', "
                            "or 'inject' when op='steer'"
                        ),
                    },
                    "field": "steer",
                }
            if not dispatch_id:
                return {
                    "error": {
                        "code": "validation_error",
                        "message": "dispatch_id is required when op='steer'",
                    },
                    "field": "dispatch_id",
                }
            if not reason:
                return {
                    "error": {
                        "code": "validation_error",
                        "message": "reason is required when op='steer'",
                    },
                    "field": "reason",
                }
            if steer == "inject" and not (directive and directive.strip()):
                return {
                    "error": {
                        "code": "validation_error",
                        "message": (
                            "directive is required when op='steer' and steer='inject'"
                        ),
                    },
                    "field": "directive",
                }
            steer_body: dict[str, Any] = {
                "op": "steer",
                "dispatch_id": dispatch_id,
                "steer": steer,
                "reason": reason,
            }
            if steer == "inject":
                steer_body["directive"] = directive
                if ttl_s is not None:
                    steer_body["ttl_s"] = ttl_s
            if caller_agent is not None:
                steer_body["actor"] = caller_agent
            record("mcp.team.steer.called")
            return await _relay(
                endpoint="/api/v1/team/dispatch",
                body=steer_body,
                record_prefix="mcp.team.steer",
            )

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

        # Agent substrates may be role/seat-less (model prefix selects transport).
        cursor_model_only = (
            op == "generate" and isinstance(model, str) and model.startswith("cursor/")
        )
        cdp_roleless = (
            op in {"generate", "to_thread"}
            and isinstance(model, str)
            and model.startswith("cdp/")
        )
        if not role and not seat and not cdp_roleless and not cursor_model_only:
            return {
                "error": {
                    "code": "validation_error",
                    "message": (
                        "exactly one of role or seat is required when "
                        "op='generate' or op='to_thread' "
                        "(except model=cdp/… or model=cursor/…)"
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
        if nest_under is not None and seat != "cursor-sdk":
            return {
                "error": {
                    "code": "nest_under_sdk_only",
                    "message": (
                        "nest_under is only valid for seat='cursor-sdk' generate/"
                        "to_thread dispatches (max nest depth 10, fail-closed on 11th)"
                    ),
                },
                "field": "nest_under",
            }
        if resume_of is not None and seat != "cursor-sdk":
            return {
                "error": {
                    "code": "resume_of_sdk_only",
                    "message": (
                        "resume_of is only valid for seat='cursor-sdk' generate/"
                        "to_thread dispatches (SDK agent-plane resume)"
                    ),
                },
                "field": "resume_of",
            }
        if lane is not None and seat != "cursor-sdk":
            return {
                "error": {
                    "code": "lane_sdk_only",
                    "message": (
                        "lane is only valid for seat='cursor-sdk' generate/"
                        "to_thread dispatches (explicit checkout isolation A|B)"
                    ),
                },
                "field": "lane",
            }
        if workspace is not None and seat != "cursor-sdk":
            return {
                "error": {
                    "code": "workspace_sdk_only",
                    "message": (
                        "workspace is only valid for seat='cursor-sdk' generate/"
                        "to_thread dispatches (named satellite git identity)"
                    ),
                },
                "field": "workspace",
            }
        lane_required_err = require_cursor_sdk_checkout_lane(
            op=op,
            seat=seat,
            role=role,
            model=model,
            lane=lane,
            nest_under=nest_under,
            resume_of=resume_of,
            contract=contract,
        )
        if lane_required_err is not None:
            return lane_required_err
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

        work_key_err = validate_work_key(work_key)
        if work_key_err is not None:
            return work_key_err

        force_err = validate_force(force, force_reason)
        if force_err is not None:
            return force_err

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
                        "use sketch, implement, wrap, conductor, pure-mechanical, or none"
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
            op, contract, packet_path, source_ref, prompt=prompt
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
            if auto_review_child is not None:
                body["auto_review_child"] = auto_review_child
            if spawn_review_provenance is not None:
                body["spawn_review_provenance"] = spawn_review_provenance
            if reuse_thread is not None:
                body["reuse_thread"] = reuse_thread
            if nest_under is not None:
                body["nest_under"] = nest_under
            if resume_of is not None:
                body["resume_of"] = resume_of
            if lane is not None:
                body["lane"] = lane
            if workspace is not None:
                body["workspace"] = workspace
            if purpose is not None:
                body["purpose"] = purpose
            if parent_thread is not None:
                body["parent_thread"] = parent_thread
            if work_key is not None:
                body["work_key"] = work_key
            if force:
                body["force"] = True
            if force_reason is not None:
                body["force_reason"] = force_reason
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
            if auto_review_child is not None:
                body["auto_review_child"] = auto_review_child
            if spawn_review_provenance is not None:
                body["spawn_review_provenance"] = spawn_review_provenance

        # Empty string ≡ absent (BIND_B); do not relay "" to Stargate.
        effort_for_relay = (reasoning_effort or "").strip() or None
        caller_agent = infer_caller_agent_for_conductor(
            caller_agent,
            contract=contract,
        )
        for key, val in (
            ("model", model),
            ("mcp", mcp),
            ("skills", skills),
            ("server_tools", server_tools),
            ("reasoning_effort", effort_for_relay),
            ("generation_options", generation_options),
            ("model_knobs", model_knobs),
            ("max_tool_turns", max_tool_turns),
            ("transcript_id", transcript_id),
            ("caller_agent", caller_agent),
            ("timeout_seconds", timeout_seconds),
            ("bus_lifecycle", bus_lifecycle),
            ("cost_intent", cost_intent),
            ("cost_intent_reason", cost_intent_reason),
            (
                "suppress_cost_warning",
                suppress_cost_warning if suppress_cost_warning else None,
            ),
        ):
            if val is not None:
                body[key] = val

        record(
            "mcp.team.dispatch.called",
            role=role,
            op=op,
            model=model or "",
            reasoning_effort=effort_for_relay or "",
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
