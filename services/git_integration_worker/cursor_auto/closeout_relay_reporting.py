"""L2 reporting-field presence checks and caller_auditable tiering at relay."""

from __future__ import annotations

import re

from services.git_integration_worker.cursor_auto.closeout_relay_common import (
    CloseoutRelayPayload,
    looks_section2,
)

_REPORTING_MISSING_SCOPE_DELTA = "reporting:missing_scope_delta"
_REPORTING_MISSING_ACCESS = "reporting:missing_access"
_REPORTING_MISSING_COVERAGE = "reporting:missing_coverage"
_REPORTING_MISSING_MODEL_ACTUAL = "reporting:missing_model_actual"
_TABLE_CELL_ROW_RE = re.compile(r"(?im)^\|\s*(?P<field>[^|]+?)\s*\|\s*(?P<value>.*?)\s*\|\s*$")
_SCOPE_DELTA_ABSENT_MARKERS: tuple[str, ...] = (
    "none — field not authored in §2 sidecar",
    "none — field not authored",
    "unauthored — not reported by executor",
    "unknown — executor emitted no §2",
)
_FIELD_ABSENT_MARKERS: tuple[str, ...] = (
    "none — field not authored in §2 sidecar",
    "unauthored — not reported by executor",
    "unknown — executor emitted no §2",
    "not reported",
    "unauthored",
)


def _extract_table_cell(body: str, field: str) -> str | None:
    for match in _TABLE_CELL_ROW_RE.finditer(body):
        if match.group("field").strip().casefold() == field.casefold():
            return match.group("value").strip()
    return None


def _cell_claims_field_absent(cell: str) -> bool:
    lowered = cell.strip().casefold()
    if not lowered or lowered in {"none", "n/a"}:
        return True
    return any(marker.casefold() in lowered for marker in _FIELD_ABSENT_MARKERS)


def _extract_named_section(body: str, *field_keys: str) -> str | None:
    from services.git_integration_worker.cursor_auto.closeout_relay_cortex_fields import (
        extract_field_section,
    )

    for field in field_keys:
        section = extract_field_section(body, field)
        if section and section.strip():
            return section.strip()
    return None


def _scope_delta_present(body: str) -> bool:
    cell = _extract_table_cell(body, "deltas_to_spec")
    if cell is not None:
        if _cell_claims_field_absent(cell):
            return False
        return bool(cell.strip())
    section = _extract_named_section(body, "deltas_to_spec")
    if section is None:
        return False
    if any(marker.casefold() in section.casefold() for marker in _SCOPE_DELTA_ABSENT_MARKERS):
        return False
    return bool(section.strip())


def _access_present(body: str) -> bool:
    cell = _extract_table_cell(body, "access")
    if cell is not None:
        return not _cell_claims_field_absent(cell)
    section = _extract_named_section(body, "access")
    return section is not None and bool(section.strip())


def _coverage_present(body: str) -> bool:
    cell = _extract_table_cell(body, "coverage")
    if cell is not None:
        return not _cell_claims_field_absent(cell)
    section = _extract_named_section(body, "coverage")
    return section is not None and bool(section.strip())


def _model_actual_present(body: str) -> bool:
    cell = _extract_table_cell(body, "model actual")
    if cell is None:
        cell = _extract_table_cell(body, "model_actual")
    if cell is not None:
        return not _cell_claims_field_absent(cell)
    section = _extract_named_section(body, "model_actual")
    return section is not None and bool(section.strip())


def missing_reporting_field_deviations(
    body: str,
    *,
    model_substitution: bool,
) -> list[str]:
    """Return deviation tokens for absent required §2 reporting checklist fields."""
    deviations: list[str] = []
    if not _scope_delta_present(body):
        deviations.append(_REPORTING_MISSING_SCOPE_DELTA)
    if not _access_present(body):
        deviations.append(_REPORTING_MISSING_ACCESS)
    if not _coverage_present(body):
        deviations.append(_REPORTING_MISSING_COVERAGE)
    if model_substitution and not _model_actual_present(body):
        deviations.append(_REPORTING_MISSING_MODEL_ACTUAL)
    return deviations


def stamp_model_actual(
    body: str,
    *,
    requested_model: str,
    resolved_model: str,
) -> str:
    """Append MODEL ACTUAL to artifact body when resolved model differs from requested."""
    if requested_model.strip().casefold() == resolved_model.strip().casefold():
        return body
    if _model_actual_present(body):
        return body
    line = f"**MODEL ACTUAL:** requested={requested_model} resolved={resolved_model}"
    return f"{body.rstrip()}\n\n{line}\n"


def amend_reporting_field_gaps(
    body: str,
    *,
    status: str,
    source: str,
    caller_auditable: bool,
    model_substitution: bool,
) -> CloseoutRelayPayload:
    """Tier missing §2 reporting fields — clamp on blind callers, deviation-only when auditable."""
    if not looks_section2(body):
        return CloseoutRelayPayload(body=body, status=status, source=source)
    deviations = missing_reporting_field_deviations(
        body,
        model_substitution=model_substitution,
    )
    if not deviations:
        return CloseoutRelayPayload(body=body, status=status, source=source)
    from services.git_integration_worker.cursor_auto.closeout_relay_effects import (
        _append_deviation_tokens,
        _rewrite_relay_status,
    )

    amended_body = _append_deviation_tokens(body, deviations)
    amended_status = status
    if not caller_auditable and status == "complete":
        amended_status = "partial"
        amended_body = _rewrite_relay_status(amended_body, amended_status)
    return CloseoutRelayPayload(
        body=amended_body,
        status=amended_status,
        source=source,
    )


__all__ = [
    "amend_reporting_field_gaps",
    "missing_reporting_field_deviations",
    "stamp_model_actual",
]
