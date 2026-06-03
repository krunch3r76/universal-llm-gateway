"""Consensus panel dispatch helpers — role specs, provider families, stamp validation.

Phase 2 (thread 1206): orchestrates ``team_dispatch`` panel members (skeptic +
reviewer, optional synthesizer tiebreaker) and builds Menu D assert attributes.
HTTP relay lives in ``services/mcp-server/tools/panel_dispatch.py``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agent_seat.registry import resolve_agent_model
from model_id import ModelId

# Guard 3: independent family := distinct provider (display labels for asserts).
_PROVIDER_FAMILY_LABEL: dict[str, str] = {
    "anthropic": "Claude",
    "openai": "GPT",
    "xai": "Grok",
    "google": "Gemini",
}

DEFAULT_PANEL_MEMBERS: tuple[tuple[str, str | None], ...] = (
    ("skeptic", None),
    ("reviewer", "openai/gpt-5.5"),
)

TIEBREAKER_ROLE = "synthesizer"
MIN_PANEL_PROVIDER_FAMILIES = 2


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
) -> tuple[PanelMemberSpec, ...]:
    """Build the default 3-family panel roster (skeptic + reviewer [+ synthesizer])."""
    specs: list[PanelMemberSpec] = [
        PanelMemberSpec(role=role, model=model) for role, model in DEFAULT_PANEL_MEMBERS
    ]
    if include_synthesizer:
        specs.append(PanelMemberSpec(role=TIEBREAKER_ROLE, model=None))
    if extra_members:
        specs.extend(PanelMemberSpec(role=r, model=m) for r, m in extra_members)
    return tuple(specs)


def effective_model_for_member(spec: PanelMemberSpec) -> str:
    """Effective model for a panel member (explicit override or role default)."""
    if spec.model:
        return spec.model
    return resolve_agent_model(spec.role)


def provider_family_label(model: str) -> str:
    """Display family label from effective model (Guard 3 — distinct provider)."""
    provider = ModelId.parse(model).provider
    return _PROVIDER_FAMILY_LABEL.get(provider, provider)


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


def build_team_dispatch_body(
    *,
    spec: PanelMemberSpec,
    messages: list[dict[str, Any]],
    dispatch_thread_id: str,
    caller_agent: str | None = None,
    system: str = "",
) -> dict[str, Any]:
    """``team_dispatch(op=generate)`` body for one panel member."""
    body: dict[str, Any] = {
        "op": "generate",
        "role": spec.role,
        "model": effective_model_for_member(spec),
        "messages": messages,
        "dispatch_thread_id": dispatch_thread_id,
        "system": system,
    }
    if caller_agent is not None:
        body["caller_agent"] = caller_agent
    return body


def admit_panel_plan(
    *,
    disposition: str,
    include_synthesizer: bool = False,
) -> PanelAdmissionPlan | dict[str, Any]:
    """Validate disposition and return member plan, or an error envelope."""
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
    members = resolve_panel_members(include_synthesizer=include_synthesizer)
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


def validate_panel_assert_attributes(attributes: dict[str, Any]) -> list[str]:
    """Return human-readable validation errors for a ``panel`` disposition stamp.

    Helper validation only (Guard 3) — not session-close audit-gate bound until
    the panel disposition detector lands.
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

    artifact = attributes.get("lead_adjudication_artifact")
    if not artifact or not str(artifact).strip():
        errors.append(
            "lead_adjudication_artifact required for panel (Guard 2); "
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
    lead_adjudication_artifact: str,
    member_models: dict[str, str],
    material: bool = True,
) -> dict[str, Any]:
    """Menu D assert ``attributes`` block (SPLIT storage — mirror on entity optional)."""
    return {
        "consensus_disposition": "panel",
        "material": material,
        "panel_families": panel_provider_families(member_models),
        "panel_executions": panel_executions,
        "decisive_falsifier": decisive_falsifier,
        "lead_adjudication_artifact": lead_adjudication_artifact,
    }


def panel_result_envelope(
    *,
    plan: PanelAdmissionPlan,
    dispatches: dict[str, Any],
    member_models: dict[str, str],
    poll_results: dict[str, Any] | None = None,
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
        "member_models": member_models,
        "dispatches": dispatches,
        "_next": (
            "Lead adjudicates panel outputs (NON-offloadable). Then assert on "
            "decision:* with build_panel_assert_attributes + lead_adjudication_artifact; "
            "poll content via pipeline(op=result, execution_id=...)."
        ),
    }
    if errors:
        out["errors"] = errors
    if poll_results:
        out["poll_results"] = poll_results
    stamp_errors = validate_panel_assert_attributes(
        {
            "consensus_disposition": "panel",
            "panel_families": out["panel_families"],
            "panel_executions": executions,
            "decisive_falsifier": "",
            "lead_adjudication_artifact": "",
        }
    )
    if stamp_errors:
        out["stamp_warnings"] = stamp_errors
    return out
