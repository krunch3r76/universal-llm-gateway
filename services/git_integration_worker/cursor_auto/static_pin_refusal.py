"""Request-local pin refusal before queue enqueue (fail-fast).

Pins decidable from the request alone — unknown wire model/escalation, body-level
pin lines — are refused at enqueue so the serial worker is not spent on a verdict
computable in milliseconds.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from services.git_integration_worker.cursor_auto.directive import effective_contract
from services.git_integration_worker.cursor_auto.wire_map import (
    BINDABLE_EFFORT_VALUES,
    assess_effort_pin,
    assess_escalation_pin,
    assess_model_pin,
)


@dataclass(frozen=True, slots=True)
class StaticPinRefusal:
    """One statically decidable pin refusal with dispatch-identical payload."""

    reason: str
    summary: str
    payload: dict[str, Any]
    contract: str


def assess_static_pin_refusal(
    *,
    desired_model: str,
    desired_effort: str,
    escalation: str | None,
    contract: str,
    body: str,
) -> StaticPinRefusal | None:
    """Return refusal when pins are decidable from the request alone."""
    resolved_contract = effective_contract(contract, body)

    model, model_block = assess_model_pin(
        desired_model,
        contract=resolved_contract,
        body=body,
    )
    if model_block is not None:
        return StaticPinRefusal(
            reason="model_pin_refused",
            summary=model_block,
            payload={
                "summary": model_block,
                "reason": "model_pin_refused",
                "requested_model": model.get("requested"),
                "bindable": list(model.get("bindable") or ()),
            },
            contract=resolved_contract,
        )

    effort, effort_block = assess_effort_pin(desired_effort, body=body)
    if effort_block is not None:
        return StaticPinRefusal(
            reason="effort_pin_refused",
            summary=effort_block,
            payload={
                "summary": effort_block,
                "reason": "effort_pin_refused",
                "requested_effort": effort.get("requested"),
                "bindable": list(BINDABLE_EFFORT_VALUES),
            },
            contract=resolved_contract,
        )

    escalation_info, escalation_block = assess_escalation_pin(
        escalation,
        body=body,
    )
    if escalation_block is not None:
        return StaticPinRefusal(
            reason="escalation_refused",
            summary=escalation_block,
            payload={
                "summary": escalation_block,
                "reason": "escalation_refused",
                "requested_escalation": escalation_info.get("requested"),
                "bindable": list(escalation_info.get("bindable") or ()),
            },
            contract=resolved_contract,
        )

    if resolved_contract in {"ask", "recon"} and (escalation or "").strip():
        summary = (
            f"{resolved_contract} does not support CDP escalation "
            f"(ask_escalation_unsupported); omit escalation= and desired_model=cdp/*."
        )
        return StaticPinRefusal(
            reason="ask_escalation_unsupported",
            summary=summary,
            payload={
                "summary": summary,
                "reason": "ask_escalation_unsupported",
                "contract": resolved_contract,
                "requested_escalation": (escalation or "").strip(),
            },
            contract=resolved_contract,
        )

    return None
