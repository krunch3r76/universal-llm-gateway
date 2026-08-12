"""Admit *reporting* — body composition separated from admit *gating*.

Hops skip the admit gate (F5: no contract grading) but must still surface
resolved model / effort / pin flags. This module owns the report text only.
"""

from __future__ import annotations

from typing import Any

from services.git_integration_worker.cursor_auto.field_parity import (
    FieldParityReport,
    render_field_parity_line,
)


def build_admit_report_body(
    *,
    model: dict[str, Any],
    effort: dict[str, Any],
    escalation: dict[str, Any],
    contract: str,
    handoff_contract: str,
    gate_action: str | None = None,
    gate_occupancy_source: str | None = None,
    directive_present: bool = False,
    continuity_hop: bool = False,
    matched_token: str | None = None,
    report_only: bool = False,
    override_rule: str | None = None,
    effort_rule: str | None = None,
    pin_flags: tuple[str, ...] = (),
    field_parity_report: FieldParityReport | None = None,
) -> str:
    """Compose the admit / admit-report body lines (no I/O, no gating)."""
    header = (
        "Auto admit-report (hop; no gate).\n"
        if report_only
        else "Auto admitted lane:cursor-auto request.\n"
    )
    body = (
        f"{header}"
        f"requested_model={model.get('requested')} "
        f"resolved={model.get('resolved_model_id')} (admit-plane)\n"
        f"model_honored={model.get('honored')} (admit-plane pin result)\n"
        f"requested_effort={effort.get('requested')} "
        f"resolved={effort.get('resolved_effort')}\n"
        f"requested_escalation={escalation.get('requested') or '(none)'} "
        f"resolved={escalation.get('resolved_escalation') or '(none)'}\n"
        f"contract={contract} "
        f"handoff={handoff_contract}\n"
        f"gate_plan={gate_action or '(none)'}\n"
        f"gate_occupancy_source={gate_occupancy_source or 'gate_only'}\n"
        f"directive={directive_present}\n"
        f"continuity_hop={str(bool(continuity_hop)).lower()} "
        f"matched_token={matched_token or 'none'}"
    )
    if override_rule:
        body += f"\n{override_rule}"
    if effort_rule:
        body += f"\n{effort_rule}"
    if pin_flags:
        body += "\nflags: " + "; ".join(pin_flags)
    if field_parity_report is not None:
        body += "\n" + render_field_parity_line(field_parity_report)
    return body
