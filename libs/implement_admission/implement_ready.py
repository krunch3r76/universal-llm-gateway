"""Pure evaluator for todo-sourced implement admission (declared-state gate)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath

from implement_admission.dense_spec_schema import (
    dense_spec_hash_uri,
    validate_dense_spec,
)

_DENSE_SPEC_RE = re.compile(r"tasks/specs/[^/\s#?]+\.md", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class ImplementReadyVerdict:
    admitted: bool
    code: str | None = None
    reason: str | None = None
    assertion_id: int | None = None


def _spec_basename(uri: str) -> str | None:
    match = _DENSE_SPEC_RE.search(uri)
    if not match:
        return None
    return PurePosixPath(match.group(0)).name


def _assertion_cites_dense_spec(
    evidence_uris: list[str] | None,
    *,
    source_uri: str | None,
) -> bool:
    if not evidence_uris:
        return False
    cited_matches = [u for u in evidence_uris if _DENSE_SPEC_RE.search(u)]
    if not cited_matches:
        return False
    source_base = _spec_basename(source_uri or "")
    if source_base is None:
        return True
    return any(_spec_basename(u) == source_base for u in cited_matches)


def _assertion_inactive(assertion: dict, *, now_iso: str) -> bool:
    if assertion.get("superseded_by"):
        return True
    valid_until = assertion.get("valid_until")
    if valid_until and str(valid_until) <= now_iso:
        return True
    return False


def _reject(code: str, reason: str) -> ImplementReadyVerdict:
    return ImplementReadyVerdict(admitted=False, code=code, reason=reason)


def evaluate_implement_ready(
    *,
    todo_id: str,
    density_triage: str | None,
    source_uri: str | None,
    implement_ready_assertion_id: int | None,
    assertion: dict | None,
    now_iso: str,
    dense_spec_uri: str | None = None,
    dense_spec_text: str | None = None,
) -> ImplementReadyVerdict:
    """Deterministic implement-readiness verdict over declared todo state."""
    triage = (density_triage or "").strip() or None
    if triage == "mechanical":
        return ImplementReadyVerdict(admitted=True)

    if triage != "judgment_required":
        return _reject(
            "implement_triage_unknown",
            f"{todo_id}: density_triage is unset or unknown — densify via a "
            "reasoning tier before implement dispatch",
        )

    if implement_ready_assertion_id is None:
        return _reject(
            "implement_not_ready_judgment_required",
            f"{todo_id}: judgment_required but implement_ready_assertion_id is "
            "absent — record an implement-ready assertion citing the dense spec",
        )

    if assertion is None:
        return _reject(
            "implement_ready_assertion_missing",
            f"{todo_id}: implement_ready_assertion_id={implement_ready_assertion_id} "
            "does not resolve to an assertion row",
        )

    if assertion.get("entity_id") != todo_id:
        return _reject(
            "implement_ready_assertion_entity_mismatch",
            f"{todo_id}: assertion {implement_ready_assertion_id} is bound to "
            f"{assertion.get('entity_id')!r}, not this todo",
        )

    if _assertion_inactive(assertion, now_iso=now_iso):
        return _reject(
            "implement_ready_assertion_inactive",
            f"{todo_id}: assertion {implement_ready_assertion_id} is superseded "
            "or expired — record a fresh implement-ready declaration",
        )

    dense_uri = (source_uri or "").strip()
    if not dense_uri:
        return _reject(
            "implement_not_ready_no_dense_spec",
            f"{todo_id}: source_uri must point at tasks/specs/{{slug}}.md before "
            "implement dispatch",
        )

    evidence = assertion.get("evidence_uris")
    if not isinstance(evidence, list):
        evidence = None
    if not _assertion_cites_dense_spec(evidence, source_uri=dense_uri):
        return _reject(
            "implement_ready_assertion_spec_uncited",
            f"{todo_id}: assertion {implement_ready_assertion_id} must cite "
            f"the dense spec ({dense_uri}) in evidence_uris",
        )

    if dense_spec_text is None:
        return _reject(
            "implement_spec_unreadable",
            f"{todo_id}: dense spec at {dense_spec_uri or dense_uri} could not "
            "be read for schema validation",
        )
    schema = validate_dense_spec(dense_spec_text)
    if not schema.passed:
        return _reject(
            "implement_spec_not_dense",
            f"{todo_id}: {dense_spec_uri or dense_uri} fails dense-spec schema "
            f"({schema.code}: {schema.reason})",
        )
    if dense_spec_hash_uri(dense_spec_text) not in (evidence or []):
        return _reject(
            "implement_spec_drifted_since_ready",
            f"{todo_id}: current spec content is not attested by assertion "
            f"{implement_ready_assertion_id} (cite spec_sha256:<hex> of the "
            "validated content; rerun the validator and refresh the assertion)",
        )

    return ImplementReadyVerdict(
        admitted=True,
        assertion_id=implement_ready_assertion_id,
    )


__all__ = ["ImplementReadyVerdict", "evaluate_implement_ready"]
