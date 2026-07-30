"""Cursor-sdk knob alignment at the Stargate admission boundary."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

from agent_seat.tool_loop_budget import API_DEFAULT_MAX_TOOL_TURNS
from cursor_capabilities import supported_knobs
from dispatch_knob_policy import recommend_knobs

from .cursor_sdk_generate_signals import (
    emit_sdk_cost_risk_warning,
    emit_sdk_knob_dropped,
)

MechanicalContract = Literal["pure-mechanical", "light-bounded", "implement"]
CostIntent = Literal["deliberate_high_cost"] | None

_COST_RISK_MODELS = frozenset(
    {"claude-opus-5", "claude-opus-4-8", "claude-sonnet-4-6", "claude-fable-5"}
)
_MECHANICAL_CONTRACTS = frozenset({"pure-mechanical", "light-bounded"})


@dataclass(frozen=True, slots=True)
class KnobOutcome:
    status: Literal["accepted", "dropped_unsupported", "invalid_value"]
    requested: str
    forwarded: str | None
    supported: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Warning:
    code: Literal[
        "sdk_cost_risk",
        "knob_dropped",
        "max_tool_turns_ignored",
    ]
    message: str
    model: str | None = None
    contract: str | None = None
    suggested_knobs: Mapping[str, str] | None = None
    suggested_model: str | None = None
    suppressed: bool | None = None
    suppression_reason: str | None = None
    cost_intent_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.model is not None:
            payload["model"] = self.model
        if self.contract is not None:
            payload["contract"] = self.contract
        if self.suggested_knobs is not None:
            payload["suggested_knobs"] = dict(self.suggested_knobs)
        if self.suggested_model is not None:
            payload["suggested_model"] = self.suggested_model
        if self.suppressed is not None:
            payload["suppressed"] = self.suppressed
        if self.suppression_reason is not None:
            payload["suppression_reason"] = self.suppression_reason
        if self.cost_intent_reason is not None:
            payload["cost_intent_reason"] = self.cost_intent_reason
        return payload


@dataclass(frozen=True, slots=True)
class AlignmentResult:
    aligned_knobs: dict[str, str]
    warnings: list[Warning] = field(default_factory=list)
    knob_resolution: dict[str, KnobOutcome] = field(default_factory=dict)

    def warnings_as_dicts(self) -> list[dict[str, Any]]:
        return [warning.to_dict() for warning in self.warnings]

    def knob_resolution_as_dicts(self) -> dict[str, dict[str, Any]]:
        return {
            name: {
                "status": outcome.status,
                "requested": outcome.requested,
                "forwarded": outcome.forwarded,
                "supported": list(outcome.supported),
            }
            for name, outcome in self.knob_resolution.items()
        }


def _wire_model_id(resolved_model: str) -> str:
    """Strip a ``cursor/`` routing prefix without re-parsing the model id."""
    if resolved_model.startswith("cursor/"):
        return resolved_model.removeprefix("cursor/")
    return resolved_model


def _resolve_knob(
    *,
    model_id: str,
    name: str,
    value: str,
    warnings: list[Warning],
    knob_resolution: dict[str, KnobOutcome],
    aligned_knobs: dict[str, str],
) -> None:
    knobs = supported_knobs(model_id)
    spec = knobs.get(name)
    supported_values = tuple(spec.accepted) if spec is not None else ()
    if spec is None:
        knob_resolution[name] = KnobOutcome(
            status="dropped_unsupported",
            requested=value,
            forwarded=None,
            supported=supported_values,
        )
        warnings.append(
            Warning(
                code="knob_dropped",
                message=f"knob {name!r} is not supported for model {model_id!r}",
                model=model_id,
            )
        )
        emit_sdk_knob_dropped(
            model_id=model_id,
            knob=name,
            requested=value,
            reason="unsupported",
        )
        return
    if value not in spec.accepted:
        knob_resolution[name] = KnobOutcome(
            status="invalid_value",
            requested=value,
            forwarded=None,
            supported=supported_values,
        )
        warnings.append(
            Warning(
                code="knob_dropped",
                message=(
                    f"knob {name!r} value {value!r} is invalid for model {model_id!r}"
                ),
                model=model_id,
            )
        )
        emit_sdk_knob_dropped(
            model_id=model_id,
            knob=name,
            requested=value,
            reason="invalid_value",
        )
        return
    knob_resolution[name] = KnobOutcome(
        status="accepted",
        requested=value,
        forwarded=value,
        supported=supported_values,
    )
    aligned_knobs[name] = value


def align_cursor_knobs(
    *,
    resolved_model: str,
    contract: MechanicalContract,
    model_knobs: Mapping[str, str] | None = None,
    cost_intent: CostIntent = None,
    suppress_cost_warning: bool = False,
    cost_intent_reason: str | None = None,
    max_tool_turns: int | None = None,
    request_id: str | None = None,
    execution_id: str | None = None,
) -> AlignmentResult:
    """Validate, drop, and warn on cursor-sdk knobs without rejecting admission."""
    model_id = _wire_model_id(resolved_model)
    warnings: list[Warning] = []
    knob_resolution: dict[str, KnobOutcome] = {}
    aligned_knobs: dict[str, str] = {}

    for name, value in (model_knobs or {}).items():
        _resolve_knob(
            model_id=model_id,
            name=name,
            value=value,
            warnings=warnings,
            knob_resolution=knob_resolution,
            aligned_knobs=aligned_knobs,
        )

    if max_tool_turns is not None:
        warnings.append(
            Warning(
                code="max_tool_turns_ignored",
                message=(
                    "max_tool_turns is not forwarded on cursor-sdk / cursor/* "
                    "dispatches — the agent loop is unbounded (completion or "
                    "outer timeout only); API roles default to "
                    f"{API_DEFAULT_MAX_TOOL_TURNS}"
                ),
                model=model_id,
                contract=contract,
            )
        )

    if contract in _MECHANICAL_CONTRACTS and model_id in _COST_RISK_MODELS:
        suppressed = (
            cost_intent == "deliberate_high_cost" or suppress_cost_warning is True
        )
        suppression_reason: str | None = None
        if cost_intent == "deliberate_high_cost":
            suppression_reason = "cost_intent=deliberate_high_cost"
        elif suppress_cost_warning:
            suppression_reason = "suppress_cost_warning=true"

        explicit_effort = (model_knobs or {}).get("effort") is not None
        rec = recommend_knobs(contract=contract)
        suggested_knobs = None if explicit_effort else rec.knob_dict()
        suggested_model = None if explicit_effort else rec.model
        message = (
            f"{model_id} on a {contract} contract is cost-risky; unspecified axes "
            "remain backend-default (partial-merge semantics unresolved)"
        )
        if not explicit_effort:
            message += (
                f"; consider model_knobs.effort={rec.effort},"
                f"thinking={rec.thinking} or {rec.model}"
            )

        emit_sdk_cost_risk_warning(
            request_id=request_id,
            execution_id=execution_id,
            model_id=model_id,
            contract=contract,
            suppressed=suppressed,
            suppression_reason=suppression_reason,
            cost_intent_reason=cost_intent_reason,
            suggested_knobs=suggested_knobs,
            suggested_model=suggested_model,
        )
        if not suppressed:
            warnings.append(
                Warning(
                    code="sdk_cost_risk",
                    message=message,
                    model=model_id,
                    contract=contract,
                    suggested_knobs=suggested_knobs,
                    suggested_model=suggested_model,
                )
            )

    return AlignmentResult(
        aligned_knobs=aligned_knobs,
        warnings=warnings,
        knob_resolution=knob_resolution,
    )
