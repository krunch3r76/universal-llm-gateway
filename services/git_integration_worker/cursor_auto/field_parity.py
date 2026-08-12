"""Admit-time field parity — consumption-based, vocabulary-free authored scan (7119 §0)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from implement_admission.field_parity_metadata import (
    parity_class_for_row_field,
    propagation_row_field_names,
)

from services.git_integration_worker.cursor_auto.authored_key_scan import (
    authored_keys_for_parity,
)
from services.git_integration_worker.cursor_auto.execute_admission import (
    EXECUTE_CONTRACT,
    ExecuteAdmission,
)
from services.git_integration_worker.cursor_auto.propagate_admission import (
    PROPAGATE_CONTRACT,
    PropagateAdmission,
)


@dataclass(frozen=True, slots=True)
class FieldParityReport:
    """Verdict rendered as ``field_parity:`` on admit-report surfaces."""

    status: str
    scope: str
    consumed: int = 0
    unconsumed: int = 0
    unknown: int = 0
    dropped_effect: tuple[str, ...] = ()
    dropped_descriptive: tuple[str, ...] = ()
    dropped_narrowing: tuple[str, ...] = ()
    unknown_tokens: tuple[str, ...] = ()
    wire_dropped: tuple[str, ...] = ()
    normalized: tuple[str, ...] = ()


def _row_field_value(row: Any, key: str) -> str:
    if row is None:
        return "?"
    val = getattr(row, key, None)
    if val is None:
        return "none"
    if isinstance(val, bool):
        return str(val).lower()
    return str(val)


def _format_drop(key: str, *, authored: str | None, row_val: str) -> str:
    if authored is None:
        return f"{key}(row={row_val})"
    return f"{key}(authored={authored} row={row_val})"


def compute_propagate_parity(
    body: str,
    admission: PropagateAdmission,
    *,
    wire_dropped: tuple[str, ...] = (),
) -> FieldParityReport:
    """Parity for ``contract: propagate`` from parser-reported consumption."""
    if admission.error is not None:
        return FieldParityReport(
            status="uncomputable(admission_failed)",
            scope="propagate_row",
            wire_dropped=wire_dropped,
        )
    if not admission.consumed_keys and admission.error is None and not admission.rows:
        return FieldParityReport(
            status="uncomputable(parser_no_report)",
            scope="propagate_row",
            wire_dropped=wire_dropped,
        )

    authored_keys, authored_values, duplicate = authored_keys_for_parity(body)
    if duplicate:
        return FieldParityReport(
            status="REFUSED",
            scope="propagate_row",
            wire_dropped=wire_dropped,
            dropped_effect=("duplicate_authorship(conflicting_shorthand_and_yaml)",),
        )

    consumed = frozenset(admission.consumed_keys)
    row = admission.rows[0] if admission.rows else None
    row_fields = propagation_row_field_names()

    dropped_effect: list[str] = []
    dropped_descriptive: list[str] = []
    dropped_narrowing: list[str] = []
    unknown_tokens: list[str] = []
    normalized: list[str] = []

    unconsumed_row_keys: set[str] = set()
    for key in authored_keys:
        if key in consumed:
            continue
        if key not in row_fields:
            unknown_tokens.append(key)
            continue
        parity_class = parity_class_for_row_field(key)
        authored_val = authored_values.get(key)
        row_val = _row_field_value(row, key)
        if parity_class == "stamped":
            normalized.append(key)
            continue
        if parity_class == "descriptive":
            dropped_descriptive.append(_format_drop(key, authored=authored_val, row_val=row_val))
            continue
        if parity_class == "narrowing":
            dropped_narrowing.append(_format_drop(key, authored=authored_val, row_val=row_val))
            continue
        if parity_class == "bound":
            continue
        # effect-class (incl. unclassified default)
        unconsumed_row_keys.add(key)
        dropped_effect.append(_format_drop(key, authored=authored_val, row_val=row_val))

    status = "ok"
    if dropped_effect:
        status = "REFUSED"
    elif dropped_descriptive or dropped_narrowing or unknown_tokens:
        status = "WARN"

    return FieldParityReport(
        status=status,
        scope="propagate_row",
        consumed=len(consumed & row_fields),
        unconsumed=len(unconsumed_row_keys),
        unknown=len(unknown_tokens),
        dropped_effect=tuple(dropped_effect),
        dropped_descriptive=tuple(dropped_descriptive),
        dropped_narrowing=tuple(dropped_narrowing),
        unknown_tokens=tuple(sorted(unknown_tokens)),
        wire_dropped=wire_dropped,
        normalized=tuple(normalized),
    )


def compute_execute_parity(
    body: str,
    admission: ExecuteAdmission,
    *,
    wire_dropped: tuple[str, ...] = (),
) -> FieldParityReport:
    """Execute contract — manifest row consumption only."""
    if admission.error is not None:
        return FieldParityReport(
            status="uncomputable(admission_failed)",
            scope="execute_row",
            wire_dropped=wire_dropped,
        )
    consumed = frozenset(admission.consumed_keys)
    authored_keys, _, duplicate = authored_keys_for_parity(body)
    if duplicate:
        return FieldParityReport(
            status="REFUSED",
            scope="execute_row",
            wire_dropped=wire_dropped,
        )
    unknown = sorted(k for k in authored_keys if k not in consumed)
    status = "ok" if not unknown else "WARN"
    return FieldParityReport(
        status=status,
        scope="execute_row",
        consumed=len(consumed),
        unconsumed=0,
        unknown=len(unknown),
        unknown_tokens=tuple(unknown),
        wire_dropped=wire_dropped,
    )


def compute_field_parity_for_job(
    *,
    body: str,
    contract: str,
    propagate_admission: PropagateAdmission | None = None,
    execute_admission: ExecuteAdmission | None = None,
    wire_dropped: tuple[str, ...] = (),
) -> FieldParityReport:
    """Dispatch parity computation by contract — always returns an explicit report."""
    normalized_contract = (contract or "answer").strip().lower()
    if normalized_contract == PROPAGATE_CONTRACT:
        if propagate_admission is None:
            return FieldParityReport(
                status="uncomputable(parser_no_report)",
                scope="propagate_row",
                wire_dropped=wire_dropped,
            )
        return compute_propagate_parity(body, propagate_admission, wire_dropped=wire_dropped)
    if normalized_contract == EXECUTE_CONTRACT:
        if execute_admission is None:
            return FieldParityReport(
                status="uncomputable(parser_no_report)",
                scope="execute_row",
                wire_dropped=wire_dropped,
            )
        return compute_execute_parity(body, execute_admission, wire_dropped=wire_dropped)
    return FieldParityReport(
        status=f"uncomputable(no_row_model)",
        scope=normalized_contract or "answer",
        wire_dropped=wire_dropped,
    )


def render_field_parity_line(report: FieldParityReport) -> str:
    """Always emit ``field_parity:`` — absence means the checker did not run."""
    head = (
        f"field_parity: status={report.status} scope={report.scope} "
        f"consumed={report.consumed} unconsumed={report.unconsumed} "
        f"unknown={report.unknown}"
    )
    lines = [head]
    if report.dropped_effect:
        lines.append(f"  dropped_effect=[{', '.join(report.dropped_effect)}]")
    if report.dropped_descriptive:
        lines.append(f"  dropped_descriptive=[{', '.join(report.dropped_descriptive)}]")
    if report.dropped_narrowing:
        lines.append(f"  dropped_narrowing=[{', '.join(report.dropped_narrowing)}]")
    if report.unknown_tokens:
        lines.append(f"  unknown=[{', '.join(report.unknown_tokens)}]")
    if report.wire_dropped:
        lines.append(f"  wire_dropped=[{', '.join(report.wire_dropped)}]")
    if report.normalized:
        lines.append(f"  normalized=[{', '.join(report.normalized)}]")
    return "\n".join(lines)


__all__ = [
    "FieldParityReport",
    "compute_field_parity_for_job",
    "compute_propagate_parity",
    "render_field_parity_line",
]
