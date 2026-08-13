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
from services.git_integration_worker.cursor_auto.envelope_fields import (
    envelope_field_names,
    parity_class_for_envelope_field,
)
from services.git_integration_worker.cursor_auto.execute_admission import (
    EXECUTE_CONTRACT,
    ExecuteAdmission,
)
from services.git_integration_worker.cursor_auto.packet_fields import (
    packet_field_names,
)
from services.git_integration_worker.cursor_auto.propagate_admission import (
    PROPAGATE_CONTRACT,
    PropagateAdmission,
)

# Contracts the bind leaves out of parity scope entirely (§AC2 state 11).
_NO_ROW_MODEL_CONTRACTS = frozenset({"answer", "confer"})


@dataclass(frozen=True, slots=True)
class FieldParityReport:
    """Verdict rendered as ``field_parity:`` on admit-report surfaces."""

    status: str
    scope: str
    consumed: int = 0
    unconsumed: int = 0
    unknown: int = 0
    deferred: int = 0
    dropped_effect: tuple[str, ...] = ()
    dropped_descriptive: tuple[str, ...] = ()
    dropped_narrowing: tuple[str, ...] = ()
    unknown_tokens: tuple[str, ...] = ()
    deferred_tokens: tuple[str, ...] = ()
    wire_dropped: tuple[str, ...] = ()
    normalized: tuple[str, ...] = ()


def _display_value(val: Any) -> str:
    if val is None:
        return "none"
    if isinstance(val, bool):
        return str(val).lower()
    return str(val)


def _row_field_value(row: Any, key: str) -> str:
    if row is None:
        return "?"
    return _display_value(getattr(row, key, None))


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


def _normalize_authored_value(raw: str | None) -> str:
    """First token of an authored value, stripped of quoting and trailing punctuation."""
    text = (raw or "").strip()
    if not text:
        return ""
    token = text.split()[0]
    return token.strip("`'\",;").lower()


def compute_envelope_parity(
    body: str,
    envelope: dict[str, Any],
    *,
    wire_dropped: tuple[str, ...] = (),
) -> FieldParityReport:
    """Parity for contracts with no row model — authored prose vs the live envelope.

    Catches the class where a knob is authored as prose in the body, never
    reaches the wire, and the job silently runs on the default. Hops have no
    admission object at all, so their parity must come from this generic scan
    rather than a contract parser (§AC5 reason 1).

    Effect-class drops are ``WARN``, not ``REFUSED``: hops are liveness-critical
    and the false-positive volume of the authored scan against real DIRECTIVE
    bodies is unmeasured (§AC6.6). Off-vocabulary keys are counted **and**
    listed; ``unknown > 0`` moves status to ``WARN`` so an authored field the
    substrate does not consume cannot hide behind ``status=ok`` (arc 7190).
    Recognised DIRECTIVE/operator-proxy prose that this envelope scope does
    not bind is ``deferred`` — listed, not WARN (wallpaper on every well-formed
    DIRECTIVE trained seats to ignore the line that catches a real drop).
    """
    authored_keys, authored_values, _ = authored_keys_for_parity(body)
    known_fields = envelope_field_names()
    packet_fields = packet_field_names()

    dropped_effect: list[str] = []
    dropped_descriptive: list[str] = []
    unknown_tokens: list[str] = []
    deferred_tokens: list[str] = []
    consumed = 0

    for key in sorted(authored_keys):
        if key not in known_fields:
            if key in packet_fields:
                deferred_tokens.append(key)
            else:
                unknown_tokens.append(key)
            continue
        authored_val = _normalize_authored_value(authored_values.get(key))
        if not authored_val:
            continue
        live_val = _display_value(envelope.get(key))
        if authored_val == live_val.strip().lower():
            consumed += 1
            continue
        drop = _format_drop(key, authored=authored_val, row_val=live_val)
        if parity_class_for_envelope_field(key) == "descriptive":
            dropped_descriptive.append(drop)
        else:
            dropped_effect.append(drop)

    status = "ok"
    if dropped_effect or dropped_descriptive or unknown_tokens:
        status = "WARN"
    return FieldParityReport(
        status=status,
        scope="envelope",
        consumed=consumed,
        unconsumed=len(dropped_effect),
        unknown=len(unknown_tokens),
        deferred=len(deferred_tokens),
        dropped_effect=tuple(dropped_effect),
        dropped_descriptive=tuple(dropped_descriptive),
        unknown_tokens=tuple(unknown_tokens),
        deferred_tokens=tuple(deferred_tokens),
        wire_dropped=wire_dropped,
    )


def compute_field_parity_for_job(
    *,
    body: str,
    contract: str,
    propagate_admission: PropagateAdmission | None = None,
    execute_admission: ExecuteAdmission | None = None,
    envelope: dict[str, Any] | None = None,
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
    if normalized_contract in _NO_ROW_MODEL_CONTRACTS or envelope is None:
        return FieldParityReport(
            status="uncomputable(no_row_model)",
            scope=normalized_contract or "answer",
            wire_dropped=wire_dropped,
        )
    return compute_envelope_parity(body, envelope, wire_dropped=wire_dropped)


def render_field_parity_line(report: FieldParityReport) -> str:
    """Always emit ``field_parity:`` — absence means the checker did not run."""
    head = (
        f"field_parity: status={report.status} scope={report.scope} "
        f"consumed={report.consumed} unconsumed={report.unconsumed} "
        f"unknown={report.unknown} deferred={report.deferred}"
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
    if report.deferred_tokens:
        lines.append(f"  deferred=[{', '.join(report.deferred_tokens)}]")
    if report.wire_dropped:
        lines.append(f"  wire_dropped=[{', '.join(report.wire_dropped)}]")
    if report.normalized:
        lines.append(f"  normalized=[{', '.join(report.normalized)}]")
    return "\n".join(lines)


__all__ = [
    "FieldParityReport",
    "compute_envelope_parity",
    "compute_field_parity_for_job",
    "compute_propagate_parity",
    "render_field_parity_line",
]
