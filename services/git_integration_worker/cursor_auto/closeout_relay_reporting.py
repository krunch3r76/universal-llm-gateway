"""L2 reporting-field presence checks and caller_auditable tiering at relay.

Presence classification distinguishes three outcomes that blind callers must not
conflate: a field the author never wrote (absent), a field that extracted cleanly
(present), and a field the projector/parser could not read (unparsed). Locate-miss
and parse-failed cell voice are unparsed — never ``reporting:missing_*``.
"""

from __future__ import annotations

import re
from typing import Literal

from services.git_integration_worker.cursor_auto.closeout_relay_common import (
    CloseoutRelayPayload,
    looks_section2,
    merge_relay_notes,
)
from services.git_integration_worker.cursor_auto.closeout_relay_cortex_fields import (
    fenced_spans,
    in_fenced_span,
)

_REPORTING_MISSING_SCOPE_DELTA = "reporting:missing_scope_delta"
_REPORTING_MISSING_ACCESS = "reporting:missing_access"
_REPORTING_MISSING_COVERAGE = "reporting:missing_coverage"
_REPORTING_MISSING_MODEL_ACTUAL = "reporting:missing_model_actual"
_REPORTING_UNPARSED_SCOPE_DELTA = "reporting:unparsed_scope_delta"
_REPORTING_UNPARSED_ACCESS = "reporting:unparsed_access"
_REPORTING_UNPARSED_COVERAGE = "reporting:unparsed_coverage"
_REPORTING_UNPARSED_MODEL_ACTUAL = "reporting:unparsed_model_actual"
_TABLE_CELL_ROW_RE = re.compile(
    r"(?im)^\|\s*(?P<field>[^|]+?)\s*\|\s*(?P<value>.*?)\s*\|\s*$"
)
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
_FieldPresence = Literal["present", "absent", "unparsed"]


def _extract_table_cell(body: str, field: str) -> str | None:
    spans = fenced_spans(body)
    for match in _TABLE_CELL_ROW_RE.finditer(body):
        if in_fenced_span(spans, match.start()):
            continue
        if match.group("field").strip().casefold() == field.casefold():
            return match.group("value").strip()
    return None


def _cell_claims_field_unparsed(cell: str) -> bool:
    """True when a projected cell is relay parse-miss / parse-failed voice."""
    lowered = cell.strip().casefold()
    if not lowered:
        return False
    if lowered.startswith("relay could not locate"):
        return True
    if lowered.startswith("parse_failed"):
        return True
    return False


def _cell_claims_field_absent(cell: str) -> bool:
    lowered = cell.strip().casefold()
    if not lowered or lowered in {"none", "n/a"}:
        return True
    if _cell_claims_field_unparsed(cell):
        return False
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


def _classify_reporting_field(body: str, *field_keys: str) -> _FieldPresence:
    """Classify a required reporting field as present, absent, or unparsed."""
    for field in field_keys:
        cell = _extract_table_cell(body, field)
        if cell is not None:
            if _cell_claims_field_unparsed(cell):
                return "unparsed"
            if _cell_claims_field_absent(cell):
                return "absent"
            return "present" if cell.strip() else "absent"
    section = _extract_named_section(body, *field_keys)
    if section is None:
        return "absent"
    return "present" if section.strip() else "absent"


def _scope_delta_presence(body: str) -> _FieldPresence:
    cell = _extract_table_cell(body, "deltas_to_spec")
    if cell is not None:
        if _cell_claims_field_unparsed(cell):
            return "unparsed"
        if _cell_claims_field_absent(cell):
            return "absent"
        return "present" if cell.strip() else "absent"
    section = _extract_named_section(body, "deltas_to_spec")
    if section is None:
        return "absent"
    if any(
        marker.casefold() in section.casefold()
        for marker in _SCOPE_DELTA_ABSENT_MARKERS
    ):
        return "absent"
    return "present" if section.strip() else "absent"


def _access_presence(body: str) -> _FieldPresence:
    return _classify_reporting_field(body, "access")


def _coverage_presence(body: str) -> _FieldPresence:
    return _classify_reporting_field(body, "coverage")


def _model_actual_presence(body: str) -> _FieldPresence:
    for field in ("model actual", "model_actual"):
        cell = _extract_table_cell(body, field)
        if cell is not None:
            if _cell_claims_field_unparsed(cell):
                return "unparsed"
            if _cell_claims_field_absent(cell):
                return "absent"
            return "present" if cell.strip() else "absent"
    section = _extract_named_section(body, "model_actual")
    if section is None:
        return "absent"
    return "present" if section.strip() else "absent"


def _scope_delta_present(body: str) -> bool:
    return _scope_delta_presence(body) == "present"


def _access_present(body: str) -> bool:
    return _access_presence(body) == "present"


def _coverage_present(body: str) -> bool:
    return _coverage_presence(body) == "present"


def _model_actual_present(body: str) -> bool:
    return _model_actual_presence(body) == "present"


def missing_reporting_field_deviations(
    body: str,
    *,
    model_substitution: bool,
) -> list[str]:
    """Return deviation tokens for genuinely absent required §2 reporting fields."""
    deviations: list[str] = []
    if _scope_delta_presence(body) == "absent":
        deviations.append(_REPORTING_MISSING_SCOPE_DELTA)
    if _access_presence(body) == "absent":
        deviations.append(_REPORTING_MISSING_ACCESS)
    if _coverage_presence(body) == "absent":
        deviations.append(_REPORTING_MISSING_COVERAGE)
    if model_substitution and _model_actual_presence(body) == "absent":
        deviations.append(_REPORTING_MISSING_MODEL_ACTUAL)
    return deviations


def unparsed_reporting_field_deviations(
    body: str,
    *,
    model_substitution: bool,
) -> list[str]:
    """Return deviation tokens for required fields present in §2 but unparseable."""
    deviations: list[str] = []
    if _scope_delta_presence(body) == "unparsed":
        deviations.append(_REPORTING_UNPARSED_SCOPE_DELTA)
    if _access_presence(body) == "unparsed":
        deviations.append(_REPORTING_UNPARSED_ACCESS)
    if _coverage_presence(body) == "unparsed":
        deviations.append(_REPORTING_UNPARSED_COVERAGE)
    if model_substitution and _model_actual_presence(body) == "unparsed":
        deviations.append(_REPORTING_UNPARSED_MODEL_ACTUAL)
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
    """Tier missing/unparsed §2 reporting fields — clamp only on genuine absence."""
    if not looks_section2(body):
        return CloseoutRelayPayload(body=body, status=status, source=source)
    missing = missing_reporting_field_deviations(
        body,
        model_substitution=model_substitution,
    )
    unparsed = unparsed_reporting_field_deviations(
        body,
        model_substitution=model_substitution,
    )
    deviations = [*missing, *unparsed]
    if not deviations:
        return CloseoutRelayPayload(body=body, status=status, source=source)
    from services.git_integration_worker.cursor_auto.closeout_relay_effects import (
        _append_deviation_tokens,
    )

    amended_body = _append_deviation_tokens(body, deviations)
    if missing and not caller_auditable and status == "complete":
        relay_note = merge_relay_notes(
            "; ".join(deviations),
            "reporting:blind_caller_missing_fields",
        )
    else:
        relay_note = merge_relay_notes("; ".join(deviations))
    return CloseoutRelayPayload(
        body=amended_body,
        status=status,
        source=source,
        relay_note=relay_note,
    )


__all__ = [
    "amend_reporting_field_gaps",
    "missing_reporting_field_deviations",
    "stamp_model_actual",
    "unparsed_reporting_field_deviations",
]
