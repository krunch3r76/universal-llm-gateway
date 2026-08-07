"""Admission for ``contract: propagate`` — operator restart requests via cursor-auto.

Operators mint structured propagation rows and ULG coordinates drain-gated
``sync_restart`` through manage.sock — not via tier-M ``execute`` + ``manage.*``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from charter_runner_store.propagation_code_ref_mint import (
    UnresolvableCodeRefError,
    admit_error_for_unresolvable_code_ref,
    require_resolvable_code_ref,
)
from deploy_identity.code_version import normalize_code_ref, resolve_code_version
from implement_admission.propagation_admit_validation import (
    LEGAL_SAFE_WINDOW_LIST,
    validate_service_slug,
)
from implement_admission.propagation_block_parser import (
    parse_propagation_block,
    propagation_block_present,
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
    r"(?im)^scope:\s*propagation\s+sync_restart\s+([a-z][a-z0-9_]*)\s*$"
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


def _rows_from_structured_block(
    body: str,
) -> tuple[tuple[PropagationRow, ...], tuple[str, ...], dict[str, Any] | None]:
    """Parse an authored ``## propagation`` block — never fall back to prose."""
    raw_rows, flags = parse_propagation_block(body)
    all_flags = tuple(flags)
    if flags:
        return (), all_flags, _error(
            "propagation_block_invalid",
            f"## propagation block rejected: {', '.join(flags)}",
            PROPAGATE_MISSING_FIX_HINT,
            invalid_flags=list(flags),
            legal_safe_window=LEGAL_SAFE_WINDOW_LIST,
        )
    if not raw_rows:
        return (), all_flags, _error(
            "propagation_block_empty",
            "## propagation block present but contained no valid rows.",
            PROPAGATE_MISSING_FIX_HINT,
        )
    rows, parse_flags = rows_from_parsed_block(raw_rows)
    all_flags = all_flags + tuple(parse_flags)
    if parse_flags or not rows:
        return (), all_flags, _error(
            "propagation_block_invalid",
            f"## propagation block rejected: {', '.join(parse_flags) or 'no valid rows'}",
            PROPAGATE_MISSING_FIX_HINT,
            invalid_flags=list(parse_flags),
            legal_safe_window=LEGAL_SAFE_WINDOW_LIST,
        )
    return tuple(rows), all_flags, None


def _rows_from_shorthand(body: str) -> tuple[PropagationRow, ...]:
    scope_match = _SCOPE_SYNC_RESTART_RE.search(body)
    service_field = _SERVICE_FIELD_RE.search(body)
    if scope_match:
        service = scope_match.group(1).lower()
    elif _SCOPE_PROPAGATION_RE.search(body) and service_field:
        service = service_field.group(1).lower()
    else:
        return ()
    service_error = validate_service_slug(service)
    if service_error:
        return ()
    code_ref_match = _CODE_REF_FIELD_RE.search(body)
    raw_ref = code_ref_match.group(1).strip() if code_ref_match else resolve_code_version()
    code_ref = require_resolvable_code_ref(
        normalize_code_ref(raw_ref), service=service
    )
    proof_class = default_proof_class(service)
    return (
        PropagationRow(
            service=service,
            code_ref=code_ref,
            safe_window=default_safe_window(service),
            proof_class=proof_class,
            proof=default_proof(service, proof_class),
            reason="operator restart request via cursor-auto",
        ),
    )


def _validate_admitted_rows(
    rows: tuple[PropagationRow, ...],
) -> tuple[tuple[PropagationRow, ...], dict[str, Any] | None]:
    """Resolve every row's code_ref; refuse the admit on the first non-object."""
    resolved: list[PropagationRow] = []
    for row in rows:
        try:
            sha = require_resolvable_code_ref(row.code_ref, service=row.service)
        except UnresolvableCodeRefError:
            return (), admit_error_for_unresolvable_code_ref(row.code_ref)
        if sha == row.code_ref:
            resolved.append(row)
        else:
            resolved.append(row.model_copy(update={"code_ref": sha}))
    return tuple(resolved), None


def admit_propagate_body(body: str) -> PropagateAdmission:
    """Resolve a ``propagate`` DIRECTIVE body into propagation rows."""
    text = body or ""
    if not _EFFECTS_EXPECTED_RE.search(text):
        return PropagateAdmission(
            error=_error(
                "propagate_effects_expected_missing",
                "contract=propagate requires effects_expected: naming the observable outcome.",
                PROPAGATE_SCOPE_FIX_HINT,
            ),
        )

    if propagation_block_present(text):
        rows, flags, block_error = _rows_from_structured_block(text)
        if block_error is not None:
            return PropagateAdmission(flags=flags, error=block_error)
        validated, ref_error = _validate_admitted_rows(rows)
        if ref_error is not None:
            return PropagateAdmission(flags=flags, error=ref_error)
        return PropagateAdmission(rows=validated, flags=flags)

    try:
        shorthand_rows = _rows_from_shorthand(text)
    except UnresolvableCodeRefError as exc:
        return PropagateAdmission(
            error=admit_error_for_unresolvable_code_ref(exc.code_ref),
        )
    if shorthand_rows:
        return PropagateAdmission(rows=shorthand_rows)

    return PropagateAdmission(
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
