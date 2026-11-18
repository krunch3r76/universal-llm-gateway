"""Mechanical-implement executor model gate (GIW admit + Stargate generate)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from cursor_capabilities import canonical_cursor_bare_id

from implement_admission.workflow_registry import (
    MECHANICAL_WORKFLOW,
    load_workflow_registry,
)

MECHANICAL_EXECUTOR_CONTRACTS: frozenset[str] = frozenset(
    {"implement", "mechanical", "pure-mechanical"}
)

IMPLEMENT_COMPOSER_WAIVER_MARKER = "invariant:implement-composer"

_STARGATE_REFUSE_CODE = "resolved_model_mismatch"
_GIW_REFUSE_CODE = "MECHANICAL_EXECUTOR_MODEL_REFUSED"


@dataclass(frozen=True, slots=True)
class MechanicalExecutorGateVerdict:
    allowed: bool
    http_status: int | None = None
    code: str | None = None
    reason: str | None = None
    validation_stage: str | None = None
    details: dict[str, Any] | None = None


def mechanical_implement_model() -> str:
    """Pinned ``cursor/…`` model from ``workflows.mechanical_implement``."""
    binding = load_workflow_registry().workflows.get(MECHANICAL_WORKFLOW)
    if binding is None or not binding.model:
        raise ValueError("workflows.mechanical_implement.model missing")
    return binding.model.strip()


def mechanical_implement_bare_id() -> str:
    return canonical_cursor_bare_id(mechanical_implement_model())


def bare_ids_match(left: str, right: str) -> bool:
    try:
        return canonical_cursor_bare_id(left) == canonical_cursor_bare_id(right)
    except ValueError:
        return left.strip().lower() == right.strip().lower()


def effective_mechanical_contract(
    *,
    handoff_contract: str | None,
    operator_contract: str | None,
    inferred_contract: str | None,
) -> str:
    """Operator contract wins; else handoff / inferred chain (same as prompt-expand)."""
    operator = (operator_contract or "").strip().lower()
    if operator:
        return operator
    for token in (handoff_contract, inferred_contract):
        if token is None:
            continue
        text = str(token).strip().lower()
        if text:
            return text
    return "none"


def is_mechanical_executor_contract(contract: str | None) -> bool:
    return (contract or "").strip().lower() in MECHANICAL_EXECUTOR_CONTRACTS


def implement_composer_waiver_valid(
    *,
    ban_override: bool,
    ban_override_reason: str | None,
    model_ban_override_event_id: str | None,
) -> bool:
    if not ban_override:
        return False
    reason = str(ban_override_reason or "").strip()
    event_id = str(model_ban_override_event_id or "").strip()
    if not reason or not event_id:
        return False
    return IMPLEMENT_COMPOSER_WAIVER_MARKER in reason


def _parent_child_resume_escape(
    *,
    resume_of: str | None,
    parent_resolved_model: str | None,
    child_wire_model: str,
    child_bare_model: str,
) -> bool:
    if not resume_of:
        return False
    parent = (parent_resolved_model or "").strip()
    if not parent:
        return False
    child_pin = child_wire_model.strip() or f"cursor/{child_bare_model}"
    return bare_ids_match(parent, child_pin)


def evaluate_mechanical_executor_gate(
    *,
    effective_contract: str,
    wire_model: str,
    resolved_bare_model: str,
    ban_override: bool = False,
    ban_override_reason: str | None = None,
    model_ban_override_event_id: str | None = None,
    resume_of: str | None = None,
    parent_resolved_model: str | None = None,
) -> MechanicalExecutorGateVerdict:
    """Return allow or refuse for mechanical-implement contracts."""
    if not is_mechanical_executor_contract(effective_contract):
        return MechanicalExecutorGateVerdict(allowed=True)

    if _parent_child_resume_escape(
        resume_of=resume_of,
        parent_resolved_model=parent_resolved_model,
        child_wire_model=wire_model,
        child_bare_model=resolved_bare_model,
    ):
        return MechanicalExecutorGateVerdict(allowed=True)

    allowed_bare = mechanical_implement_bare_id()
    child_bare = resolved_bare_model.strip().lower()
    wire = wire_model.strip() or f"cursor/{child_bare}"

    if implement_composer_waiver_valid(
        ban_override=ban_override,
        ban_override_reason=ban_override_reason,
        model_ban_override_event_id=model_ban_override_event_id,
    ):
        return MechanicalExecutorGateVerdict(allowed=True)

    if bare_ids_match(wire, allowed_bare):
        return MechanicalExecutorGateVerdict(allowed=True)

    return MechanicalExecutorGateVerdict(
        allowed=False,
        http_status=422,
        code=_GIW_REFUSE_CODE,
        reason=(
            f"mechanical contract {effective_contract!r} requires executor model "
            f"{mechanical_implement_model()!r}; got {wire!r}"
        ),
        validation_stage="contract_model",
        details={
            "effective_contract": effective_contract,
            "requested_model": wire,
            "required_model": mechanical_implement_model(),
            "resolved_bare_model": child_bare,
            "allowed_bare_model": allowed_bare,
            "retryable": False,
            "waiver_marker": IMPLEMENT_COMPOSER_WAIVER_MARKER,
        },
    )


def refuse_stargate_mechanical_executor(
    *,
    job: str | None,
    contract: str | None,
    requested_model: str | None,
    resolved_model: str,
) -> None:
    """Raise ``ValueError('mechanical_executor_refused')`` for team_dispatch defaults."""
    effective = (job or contract or "").strip().lower()
    if not is_mechanical_executor_contract(effective):
        return
    requested = (requested_model or "").strip()
    if not requested:
        return
    if bare_ids_match(requested, resolved_model):
        if bare_ids_match(resolved_model, mechanical_implement_model()):
            return
        raise ValueError("mechanical_executor_refused")
    raise ValueError("mechanical_executor_refused")


def stargate_mechanical_executor_error(request_id: str) -> dict[str, Any]:
    """Build reject envelope for ``mechanical_executor_refused``."""
    required = mechanical_implement_model()
    return {
        "admitted": False,
        "status_code": 422,
        "code": _STARGATE_REFUSE_CODE,
        "field": "model",
        "reason": (
            f"mechanical implement job refused: resolved model must be {required!r} "
            "for explicit pins on mechanical contracts"
        ),
        "details": {
            "request_id": request_id,
            "required_model": required,
            "retryable": False,
        },
    }