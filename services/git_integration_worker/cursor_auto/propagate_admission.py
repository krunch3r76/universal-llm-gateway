"""Admission for ``contract: propagate`` — operator restart requests via cursor-auto.

Operators mint structured propagation rows and ULG coordinates drain-gated
``sync_restart`` through manage.sock — not via tier-M ``execute`` + ``manage.*``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from deploy_identity.code_version import normalize_code_ref, resolve_code_version
from implement_admission.propagation_block_parser import (
    propagation_rows_from_markdown_sources,
)
from implement_admission.propagation_row import (
    PropagationRow,
    default_proof,
    default_proof_class,
    default_safe_window,
    row_from_mapping,
    rows_from_parsed_block,
)

from services.git_integration_worker.cursor_auto.fix_hints import (
    PROPAGATE_MISSING_FIX_HINT,
    PROPAGATE_SCOPE_FIX_HINT,
)

PROPAGATE_CONTRACT = "propagate"

_SCOPE_PROPAGATION_RE = re.compile(r"(?im)^scope:\s*propagation\b")
_SCOPE_SYNC_RESTART_RE = re.compile(
    r"(?im)^scope:\s*propagation\s+sync_restart\s+([a-z][a-z0-9_]*)"
)
_SERVICE_FIELD_RE = re.compile(r"(?im)^service:\s*([a-z][a-z0-9_]*)")
_CODE_REF_FIELD_RE = re.compile(r"(?im)^code_ref:\s*(\S+)")
_EFFECTS_EXPECTED_RE = re.compile(r"(?im)^effects_expected:\s*\S+")


@dataclass(frozen=True, slots=True)
class PropagateAdmission:
    """Verdict for one ``propagate`` body."""

    rows: tuple[PropagationRow, ...] = ()
    flags: tuple[str, ...] = ()
    error: dict[str, Any] | None = None

    @property
    def approved(self) -> bool:
        return self.error is None and bool(self.rows)


def _error(reason: str, summary: str, fix_hint: str, **extra: Any) -> dict[str, Any]:
    return {"reason": reason, "summary": summary, "fix_hint": fix_hint, **extra}


def _rows_from_yaml_body(body: str) -> tuple[tuple[PropagationRow, ...], tuple[str, ...]]:
    raw_rows, flags = propagation_rows_from_markdown_sources(body)
    if not raw_rows:
        return (), tuple(flags)
    rows, parse_flags = rows_from_parsed_block(raw_rows)
    return tuple(rows), tuple(flags) + tuple(parse_flags)


def _rows_from_shorthand(body: str) -> tuple[PropagationRow, ...]:
    scope_match = _SCOPE_SYNC_RESTART_RE.search(body)
    service_field = _SERVICE_FIELD_RE.search(body)
    if scope_match:
        service = scope_match.group(1).lower()
    elif _SCOPE_PROPAGATION_RE.search(body) and service_field:
        service = service_field.group(1).lower()
    else:
        return ()
    code_ref_match = _CODE_REF_FIELD_RE.search(body)
    raw_ref = code_ref_match.group(1).strip() if code_ref_match else resolve_code_version()
    code_ref = normalize_code_ref(raw_ref)
    return (
        PropagationRow(
            service=service,
            code_ref=code_ref,
            safe_window=default_safe_window(service),
            proof=default_proof(service),
            proof_class=default_proof_class(service),
            reason="operator restart request via cursor-auto",
        ),
    )


def admit_propagate_body(body: str) -> PropagateAdmission:
    """Resolve a ``propagate`` DIRECTIVE body into propagation rows."""
    if not _EFFECTS_EXPECTED_RE.search(body or ""):
        return PropagateAdmission(
            error=_error(
                "propagate_effects_expected_missing",
                "contract=propagate requires effects_expected: naming the observable outcome.",
                PROPAGATE_SCOPE_FIX_HINT,
            ),
        )
    yaml_rows, flags = _rows_from_yaml_body(body or "")
    if yaml_rows:
        return PropagateAdmission(rows=yaml_rows, flags=flags)
    shorthand_rows = _rows_from_shorthand(body or "")
    if shorthand_rows:
        return PropagateAdmission(rows=shorthand_rows, flags=flags)
    return PropagateAdmission(
        flags=flags,
        error=_error(
            "propagate_rows_missing",
            (
                "contract=propagate requires a ## propagation YAML block or "
                "scope: propagation sync_restart <service> (with optional code_ref:)."
            ),
            PROPAGATE_MISSING_FIX_HINT,
        ),
    )


def rows_from_admission_payload(
    payload: dict[str, Any],
) -> tuple[tuple[PropagationRow, ...], tuple[str, ...]]:
    """Materialize rows from structured ``propagation`` list in a closeout payload."""
    raw = payload.get("propagation")
    if not isinstance(raw, list):
        return (), ()
    rows = tuple(
        row_from_mapping(item) for item in raw if isinstance(item, dict)
    )
    return rows, ()


__all__ = [
    "PROPAGATE_CONTRACT",
    "PropagateAdmission",
    "admit_propagate_body",
    "rows_from_admission_payload",
]
