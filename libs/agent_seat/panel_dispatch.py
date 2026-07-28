"""Consensus panel dispatch helpers — role specs, provider families, stamp validation.

Phase 2 (thread 1206): orchestrates ``team_dispatch(op=generate)`` panel members
(skeptic + reviewer, optional synthesizer tiebreaker) and builds Menu D assert
attributes. Generate-only by design — no ``to_thread``/``handoff`` fan-out (Guard 2:
lead adjudication precedes any bus delivery). HTTP relay lives in
``services/mcp-server/tools/panel_dispatch.py``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from llm_adapters.capability_dispatch import project_knob_resolution
from model_id import ModelId

from agent_seat.profiles import get_profile, get_role
from agent_seat.registry import resolve_agent_model
from agent_seat.role_entity_sync import resolve_dispatch_capabilities

# Guard 3: independent family := distinct provider (display labels for asserts).
# Google/Gemini is optional third-family — not required for default panel coverage.
_PROVIDER_FAMILY_LABEL: dict[str, str] = {
    "anthropic": "Claude",
    "openai": "GPT",
    "xai": "Grok",
    "google": "Gemini",
}

DEFAULT_PANEL_MEMBERS: tuple[tuple[str, str | None], ...] = (
    ("skeptic", None),
    ("reviewer", "cursor/gpt-5.6-terra"),
)

TIEBREAKER_ROLE = "synthesizer"
MIN_PANEL_PROVIDER_FAMILIES = 2

# Canonical Menu D adjudication-artifact attribute key (rename thread 1268 op-2).
# ``lead_adjudication_artifact`` is the deprecated alias: the read path accepts
# either key so assertions written before the rename remain valid; the write
# path emits only the canonical key.
PANEL_ADJUDICATION_KEY = "panel_adjudication_artifact"
_DEPRECATED_ADJUDICATION_KEY = "lead_adjudication_artifact"


def read_adjudication_artifact(attributes: dict[str, Any]) -> Any:
    """Read the panel adjudication artifact, accepting the deprecated alias.

    Canonical key is ``panel_adjudication_artifact``; ``lead_adjudication_artifact``
    is accepted as a backward-compat alias so pre-rename DB assertions still
    satisfy Guard 2 detectors.
    """
    return attributes.get(PANEL_ADJUDICATION_KEY) or attributes.get(
        _DEPRECATED_ADJUDICATION_KEY
    )


@dataclass(frozen=True)
class PanelMemberSpec:
    role: str
    model: str | None = None


@dataclass(frozen=True)
class PanelAdmissionPlan:
    """Resolved panel member list for a ``disposition=panel`` run."""

    disposition: str
    members: tuple[PanelMemberSpec, ...]


def resolve_panel_members(
    *,
    include_synthesizer: bool = False,
    extra_members: list[tuple[str, str | None]] | None = None,
    member_models: dict[str, str] | None = None,
) -> tuple[PanelMemberSpec, ...]:
    """Build the default 3-family panel roster (skeptic + reviewer [+ synthesizer]).

    ``extra_members`` is a **library-only** hook for programmatic callers. The MCP
    ``panel_dispatch`` tool intentionally does NOT expose or pass it — the MCP
    surface stays a fixed roster (skeptic + reviewer [+ synthesizer]).

    ``member_models`` (role → provider/model) overrides the model bound to a
    roster member without changing the roster itself. Overrides participate in
    family resolution (friction 23301: per-role overrides were previously
    invisible to the ≥2-family gate). Keys must name roster roles; values are
    validated against role ``allowed_models`` in ``admit_panel_plan``.
    """
    specs: list[PanelMemberSpec] = [
        PanelMemberSpec(role=role, model=model) for role, model in DEFAULT_PANEL_MEMBERS
    ]
    if include_synthesizer:
        specs.append(PanelMemberSpec(role=TIEBREAKER_ROLE, model=None))
    if extra_members:
        specs.extend(PanelMemberSpec(role=r, model=m) for r, m in extra_members)
    if member_models:
        specs = [
            PanelMemberSpec(role=s.role, model=member_models.get(s.role, s.model))
            for s in specs
        ]
    return tuple(specs)


def effective_model_for_member(spec: PanelMemberSpec) -> str:
    """Effective model for a panel member (explicit override or role default)."""
    if spec.model:
        return spec.model
    return resolve_agent_model(spec.role)


def verify_panel_role_model_resolution(
    roles: tuple[str, ...] = ("skeptic", "reviewer", TIEBREAKER_ROLE),
) -> list[str]:
    """Guard: MCP-side ``resolve_agent_model`` must match yaml role/platform SOT.

    Stargate admits ``op=generate`` with ``model`` omitted using the role's
    ``(default_family, default_platform)`` profile; ``member_models`` and Guard 3
    ``panel_families`` are computed MCP-side from ``effective_model_for_member``.
    Divergence would stamp the wrong provider families on Menu D asserts.
    """
    errors: list[str] = []
    for role in roles:
        try:
            rp = get_role(role)
        except KeyError:
            errors.append(f"unknown panel role {role!r}")
            continue
        try:
            profile = get_profile(rp.default_family, rp.default_platform)
        except KeyError:
            errors.append(
                f"{role}: no profile for ({rp.default_family}, {rp.default_platform})"
            )
            continue
        resolved = resolve_agent_model(role)
        role_default = rp.default_model or profile.default_model
        if role_default and resolved != role_default:
            errors.append(
                f"{role}: resolve_agent_model={resolved!r} != role default {role_default!r}"
            )
        if profile.default_model and role_default != profile.default_model:
            errors.append(
                f"{role}: role default {role_default!r} != platform profile "
                f"{profile.default_model!r}"
            )
        if ModelId.parse(resolved).provider != profile.provider:
            errors.append(
                f"{role}: provider mismatch resolved={resolved!r} profile={profile.provider!r}"
            )
    for spec in resolve_panel_members(include_synthesizer=True):
        if not spec.model:
            continue
        rp = get_role(spec.role)
        if rp.allowed_models and spec.model not in rp.allowed_models:
            errors.append(
                f"{spec.role}: panel override {spec.model!r} not in allowed_models"
            )
    return errors


def provider_family_label(model: str) -> str:
    """Display family label from effective model (Guard 3 — distinct provider).

    Cursor-substrate models use weight-class family (gpt→GPT, grok→Grok), not
    the substrate provider label ``cursor``.
    """
    from implement_admission.check_review_substrate import independence_family

    family = independence_family(model)
    return _PROVIDER_FAMILY_LABEL.get(family, family)


def panel_provider_families(member_models: dict[str, str]) -> list[str]:
    """Distinct provider-family labels for panel_executions keys (role → model)."""
    seen: set[str] = set()
    out: list[str] = []
    for model in member_models.values():
        label = provider_family_label(model)
        if label not in seen:
            seen.add(label)
            out.append(label)
    return out


def member_dispatch_thread_id(base: str, role: str) -> str:
    """Per-member compaction key — distinct thread per panel role (RC1 fix)."""
    return f"{base}:{role}"


def lint_panel_messages(messages: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Reject block-array message content before any paid admission."""
    for message in messages:
        if isinstance(message.get("content"), list):
            return {
                "error": {
                    "code": "validation_error",
                    "message": (
                        "panel_dispatch messages[].content must be a string, "
                        "not a block array"
                    ),
                }
            }
    return None


def build_team_dispatch_body(
    *,
    spec: PanelMemberSpec,
    dispatch_thread_id: str,
    caller_agent: str | None = None,
    system: str = "",
    reasoning_effort: str | None = None,
    generation_options: dict[str, Any] | None = None,
    max_tool_turns: int | None = None,
    transcript_id: str | None = None,
    timeout_seconds: int | None = None,
) -> dict[str, Any]:
    """``team_dispatch(op=generate)`` body for one panel member."""
    body: dict[str, Any] = {
        "op": "generate",
        "dispatch_thread_id": dispatch_thread_id,
        "contract": "light-bounded",
        "system": system,
    }
    model = spec.model
    if model is not None and ModelId.parse(model).backend_type == "cursor_sdk":
        # cursor/* cannot combine with role= (substrate_model_role_conflict).
        body["seat"] = "cursor-sdk"
        body["model"] = model
    else:
        body["role"] = spec.role
        if model is not None:
            body["model"] = model
    for key, val in (
        ("caller_agent", caller_agent),
        ("reasoning_effort", reasoning_effort),
        ("generation_options", generation_options),
        ("max_tool_turns", max_tool_turns),
        ("transcript_id", transcript_id),
        ("timeout_seconds", timeout_seconds),
    ):
        if val is not None:
            body[key] = val
    return body


def admit_panel_plan(
    *,
    disposition: str,
    include_synthesizer: bool = False,
    member_models: dict[str, str] | None = None,
) -> PanelAdmissionPlan | dict[str, Any]:
    """Validate disposition and return member plan, or an error envelope.

    The MCP ``panel_dispatch`` tool calls this with the fixed roster only —
    ``extra_members`` (a ``resolve_panel_members`` library-only hook) is never
    threaded through the MCP surface. ``member_models`` (role → model) IS an
    MCP-exposed override: it rebinds models on roster members and is honored
    by the ≥2-family gate below (friction 23301). Unknown roles and models
    outside a role's ``allowed_models`` reject before any paid admission.
    """
    if disposition != "panel":
        return {
            "error": {
                "code": "validation_error",
                "message": (
                    "panel_dispatch runs only when consensus_disposition=panel; "
                    f"got {disposition!r}"
                ),
            }
        }
    members = resolve_panel_members(
        include_synthesizer=include_synthesizer,
        member_models=member_models,
    )
    if member_models:
        roster_roles = {m.role for m in members}
        unknown = sorted(set(member_models) - roster_roles)
        if unknown:
            return {
                "error": {
                    "code": "validation_error",
                    "message": (
                        f"member_models names non-roster roles {unknown!r}; "
                        f"roster is {sorted(roster_roles)!r}"
                    ),
                },
                "field": "member_models",
            }
        for role, model in member_models.items():
            try:
                role_profile = get_role(role)
            except KeyError:
                role_profile = None
            allowed = role_profile.allowed_models if role_profile else None
            if allowed and model not in allowed:
                return {
                    "error": {
                        "code": "validation_error",
                        "message": (
                            f"member_models[{role!r}]={model!r} is not in the "
                            f"role's allowed_models {list(allowed)!r}"
                        ),
                    },
                    "field": "member_models",
                }
    models = {m.role: effective_model_for_member(m) for m in members}
    families = panel_provider_families(models)
    if len(families) < MIN_PANEL_PROVIDER_FAMILIES:
        return {
            "error": {
                "code": "validation_error",
                "message": (
                    f"panel requires >= {MIN_PANEL_PROVIDER_FAMILIES} distinct provider "
                    f"families; resolved {families!r} from {models!r}"
                ),
            },
        }
    return PanelAdmissionPlan(disposition=disposition, members=members)


def count_execution_evidence_uris(uris: list[str] | None) -> int:
    """Count ``execution:`` URIs on a Menu D assert (session-close gate uses this)."""
    if not uris:
        return 0
    return sum(1 for u in uris if isinstance(u, str) and u.startswith("execution:"))


def validate_panel_assert_attributes(attributes: dict[str, Any]) -> list[str]:
    """Return human-readable validation errors for a ``panel`` disposition stamp.

    Helper validation (Guard 3); session-close ``panel_disposition_incomplete``
    detector reuses this — assert-time does not reject on these errors.
    """
    errors: list[str] = []
    disposition = attributes.get("consensus_disposition")
    if disposition != "panel":
        return errors

    families = attributes.get("panel_families") or []
    if not isinstance(families, list):
        errors.append("panel_families must be a list")
    elif len({f for f in families if isinstance(f, str)}) < MIN_PANEL_PROVIDER_FAMILIES:
        errors.append(
            f"panel_families needs >= {MIN_PANEL_PROVIDER_FAMILIES} distinct providers"
        )

    falsifier = attributes.get("decisive_falsifier")
    if not falsifier or not str(falsifier).strip():
        errors.append("decisive_falsifier required for panel disposition")

    artifact = read_adjudication_artifact(attributes)
    if not artifact or not str(artifact).strip():
        errors.append(
            "panel_adjudication_artifact required for panel (Guard 2); "
            "else stamp steelman-only"
        )

    executions = attributes.get("panel_executions") or attributes.get("panel_tally")
    if (
        not isinstance(executions, dict)
        or len(executions) < MIN_PANEL_PROVIDER_FAMILIES
    ):
        errors.append(
            "panel_executions (role→execution_id) needs >= 2 entries for panel"
        )

    return errors


def build_panel_assert_attributes(
    *,
    panel_executions: dict[str, str],
    decisive_falsifier: str,
    panel_adjudication_artifact: str,
    member_models: dict[str, str],
    material: bool = True,
) -> dict[str, Any]:
    """Build Menu D ``attributes`` block for ``cortex(tool="assert", attributes=...)``.

    Per consensus-steelman-posture §3.1 (confirmed, thread 1206): the
    **assertion** is the source of truth for consensus_disposition and all
    panel metadata.  Pass the returned dict as ``attributes=`` on the assert
    call — do NOT use ``entity_update(attributes=...)`` as the primary write.
    Entity attributes may optionally mirror the latest state for cheap
    ``entity_get`` reads, but they are a derived cache: session-close detectors
    and audits query the non-superseded assertion, NEVER the entity blob.
    """
    return {
        "consensus_disposition": "panel",
        "material": material,
        "panel_families": panel_provider_families(member_models),
        "panel_executions": panel_executions,
        "decisive_falsifier": decisive_falsifier,
        PANEL_ADJUDICATION_KEY: panel_adjudication_artifact,
    }


def _usage_tokens(usage: dict[str, Any] | None) -> tuple[int, int]:
    if not usage:
        return 0, 0
    tokens_in = (
        usage.get("tokens_in")
        or usage.get("prompt_tokens")
        or usage.get("input_tokens")
        or 0
    )
    tokens_out = (
        usage.get("tokens_out")
        or usage.get("completion_tokens")
        or usage.get("output_tokens")
        or 0
    )
    return int(tokens_in), int(tokens_out)


def _tokens_from_poll_result(poll_result: dict[str, Any]) -> tuple[int, int]:
    if poll_result.get("error"):
        return 0, 0
    result = poll_result.get("result")
    if isinstance(result, dict):
        usage = result.get("usage")
        if isinstance(usage, dict):
            return _usage_tokens(usage)
    return 0, 0


def member_status_from_poll(
    dispatch_payload: Any,
    poll_result: dict[str, Any] | None,
    *,
    polled: bool,
) -> str:
    """Map dispatch + optional pipeline poll to panel member status."""
    if isinstance(dispatch_payload, dict) and dispatch_payload.get("error"):
        return "failed"
    if not isinstance(dispatch_payload, dict) or not dispatch_payload.get(
        "execution_id"
    ):
        return "failed"
    if not polled or poll_result is None:
        return "running"
    if poll_result.get("error"):
        return "failed"
    pipe_status = poll_result.get("status")
    if pipe_status == "completed":
        return "complete"
    if pipe_status == "failed":
        return "failed"
    return "running"


def aggregate_panel_status(member_status: dict[str, str], *, polled: bool) -> str:
    if not polled:
        return "dispatched"
    if any(status == "running" for status in member_status.values()):
        return "partial"
    if any(status == "failed" for status in member_status.values()):
        return "failed"
    return "complete"


def build_panel_poll_summary(
    *,
    dispatches: dict[str, Any],
    poll_results: dict[str, Any] | None,
    polled: bool,
) -> dict[str, Any]:
    """Poll envelope: status, per-member status, aggregate tokens (E6/E8)."""
    member_status: dict[str, str] = {}
    tokens_in = 0
    tokens_out = 0
    in_flight: list[str] = []

    for role, dispatch_payload in dispatches.items():
        poll_result = (poll_results or {}).get(role) if polled else None
        status = member_status_from_poll(dispatch_payload, poll_result, polled=polled)
        member_status[role] = status
        if polled and poll_result is not None:
            tin, tout = _tokens_from_poll_result(poll_result)
            tokens_in += tin
            tokens_out += tout
        if status == "running" and isinstance(dispatch_payload, dict):
            execution_id = dispatch_payload.get("execution_id")
            if execution_id:
                in_flight.append(str(execution_id))

    summary: dict[str, Any] = {
        "status": aggregate_panel_status(member_status, polled=polled),
        "member_status": member_status,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
    }
    if polled:
        summary["do_not_resubmit"] = bool(in_flight)
        summary["in_flight_execution_ids"] = in_flight
    return summary


def panel_result_envelope(
    *,
    plan: PanelAdmissionPlan,
    dispatches: dict[str, Any],
    member_models: dict[str, str],
    poll_results: dict[str, Any] | None = None,
    submission_plan: list[dict[str, Any]] | None = None,
    poll_summary: dict[str, Any] | None = None,
    reasoning_effort: str | None = None,
    requested_max_output: int | None = None,
) -> dict[str, Any]:
    """Structured panel helper output for lead adjudication + Menu D assert."""
    executions: dict[str, str] = {}
    errors: dict[str, Any] = {}
    for role, payload in dispatches.items():
        if isinstance(payload, dict) and payload.get("execution_id"):
            executions[role] = str(payload["execution_id"])
        elif isinstance(payload, dict) and "error" in payload:
            errors[role] = payload["error"]
        else:
            errors[role] = payload

    out: dict[str, Any] = {
        "disposition": plan.disposition,
        "panel_families": panel_provider_families(member_models),
        "panel_executions": executions,
        "panel_capabilities": {
            role: resolve_dispatch_capabilities(model=model)
            for role, model in member_models.items()
        },
        "member_models": member_models,
        "dispatches": dispatches,
        "_next": (
            "Lead adjudicates panel outputs (NON-offloadable). Before adjudication, "
            "inspect member_knob_resolution[*].status/parity/notes; do not infer "
            "cross-provider parity. Then assert on decision:* with "
            "build_panel_assert_attributes + panel_adjudication_artifact; poll "
            "content via pipeline(op=result, execution_id=...)."
        ),
    }
    if errors:
        out["errors"] = errors
    if poll_results:
        out["poll_results"] = poll_results
    if submission_plan is not None:
        out["submission_plan"] = submission_plan
    if poll_summary is not None:
        out.update(poll_summary)
    out["member_knob_resolution"] = {
        role: project_knob_resolution(
            resolved_model=model,
            requested_effort=reasoning_effort,
            requested_max_output=requested_max_output,
        )
        for role, model in member_models.items()
    }
    stamp_errors = validate_panel_assert_attributes(
        {
            "consensus_disposition": "panel",
            "panel_families": out["panel_families"],
            "panel_executions": executions,
            "decisive_falsifier": "",
            PANEL_ADJUDICATION_KEY: "",
        }
    )
    if stamp_errors:
        out["stamp_warnings"] = stamp_errors
    return out
