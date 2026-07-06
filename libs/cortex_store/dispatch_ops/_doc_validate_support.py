"""Shared helpers for doc_validate preflight resolution and report shaping."""

from __future__ import annotations

from typing import Any

from implement_admission import dense_spec_schema as _schema
from implement_admission.dense_spec_schema import (
    dense_spec_hash_uri,
    validate_dense_spec,
)
from implement_admission.gate_distillation import read_dense_spec_text
from implement_admission.recon_waiver import parse_recon_waiver, recon_waived_bool
from implement_admission.source_ref import parse_source_ref
from implement_admission.spec import SourceKind

from .adapters._implement_ready_preflight import (
    _coerce_assertion_id,
    _decode_attributes,
    _pin_needs_resolution,
    _resolve_fresh_assertion,
    _resolve_skeptic_outcome,
    _select_cited_spec_uri,
    _spec_path_from_uri,
)
from .ops_assertions import _op_assertion_get
from .ops_entities import _op_entity_get

_AUTHORING_TODO = "todo:__doc_validate_authoring__"


def err422(message: str) -> dict[str, Any]:
    return {"error": message, "status_code": 422}


def extract_spec_sha256_token(evidence: list[str] | None) -> str | None:
    if not evidence:
        return None
    for entry in evidence:
        if isinstance(entry, str) and entry.startswith("spec_sha256:"):
            return entry
    return None


def authoring_preflight_kwargs(
    *,
    spec_text: str,
    spec_uri: str | None,
) -> dict[str, Any]:
    """Bare-spec (no todo) validation context — text=/path= callers.

    No real todo backs this call, so the todo-linkage gates (2-5, 7, 10-13)
    must not be evaluated against fabricated stand-ins; ``authoring_mode``
    tells ``preflight_implement_ready`` to report them ``not_applicable``
    instead. Only the spec-content gates (6, 8, 9) are meaningful here.
    """
    uri = spec_uri or "tasks/specs/__authoring__.md"
    return {
        "todo_id": _AUTHORING_TODO,
        "density_triage": "judgment_required",
        "source_uri": uri,
        "implement_ready_assertion_id": None,
        "assertion": None,
        "dense_spec_uri": uri,
        "dense_spec_text": spec_text,
        "files_expected": [],
        "acceptance_criteria": [],
        "entity_name": "Authoring validation",
        "skeptic_ratified": False,
        "recon_waived": False,
        "authoring_mode": True,
    }


def resolve_todo_preflight_kwargs(source_ref: str, *, now_iso: str) -> dict[str, Any]:
    ref = parse_source_ref(source_ref)
    if ref.source_kind != SourceKind.TODO.value:
        return err422(f"doc_validate source_ref must be todo:…; got {source_ref!r}")

    todo_id = ref.canonical_ref
    entity = _op_entity_get(entity_id=todo_id, intent="full")
    if not entity or "error" in entity:
        return err422(f"todo not found: {todo_id}")

    attrs = _decode_attributes(entity.get("attributes"))
    aid = _coerce_assertion_id(attrs.get("implement_ready_assertion_id"))
    assertion: dict[str, Any] | None = None
    if aid is not None:
        loaded = _op_assertion_get(assertion_id=aid)
        if isinstance(loaded, dict) and "error" not in loaded:
            assertion = loaded

    resolution: dict[str, Any] | None = None
    triage = (attrs.get("density_triage") or "").strip() or None
    if triage == "judgment_required" and _pin_needs_resolution(
        assertion, todo_id=todo_id, now_iso=now_iso
    ):
        fresh = _resolve_fresh_assertion(todo_id=todo_id, now_iso=now_iso)
        if fresh is not None:
            fresh_aid, fresh_assertion = fresh
            resolution = {
                "pinned_assertion_id": aid,
                "effective_assertion_id": fresh_aid,
                "used_fresh_assertion_fallback": True,
            }
            aid, assertion = fresh_aid, fresh_assertion

    cited_uri: str | None = None
    spec_text: str | None = None
    evidence = assertion.get("evidence_uris") if assertion else None
    if isinstance(evidence, list):
        cited_uri = _select_cited_spec_uri(
            evidence, source_uri=entity.get("source_uri")
        )
        if cited_uri is not None:
            spec_path = _spec_path_from_uri(cited_uri)
            if spec_path is not None:
                try:
                    spec_text = read_dense_spec_text(spec_path)
                except Exception:
                    spec_text = None

    raw_files = attrs.get("files_expected")
    files_expected = raw_files if isinstance(raw_files, list) else []
    raw_acs = attrs.get("acceptance_criteria")
    acceptance_criteria = raw_acs if isinstance(raw_acs, list) else []
    raw_waived = attrs.get("recon_waived")
    recon_waived = recon_waived_bool(raw_waived)
    recon_waiver = parse_recon_waiver(raw_waived)

    spec_hash_uri = dense_spec_hash_uri(spec_text) if spec_text else None
    skeptic_ratified = False
    if triage == "judgment_required":
        skeptic_ratified = _resolve_skeptic_outcome(
            todo_id=todo_id,
            spec_hash_uri=spec_hash_uri,
            now_iso=now_iso,
        )

    return {
        "todo_id": todo_id,
        "density_triage": attrs.get("density_triage"),
        "source_uri": entity.get("source_uri"),
        "implement_ready_assertion_id": aid,
        "assertion": assertion,
        "dense_spec_uri": cited_uri,
        "dense_spec_text": spec_text,
        "files_expected": files_expected,
        "acceptance_criteria": acceptance_criteria,
        "entity_name": entity.get("name"),
        "resolution": resolution,
        "skeptic_ratified": skeptic_ratified,
        "recon_waived": recon_waived,
        "recon_waiver": recon_waiver.to_gate_sibling() if recon_waiver else None,
    }


def enrich_gates(gates: list[dict[str, Any]], spec_text: str) -> list[dict[str, Any]]:
    verdict = validate_dense_spec(spec_text)
    hints = {
        key: _schema._SECTION_ACCEPTED_PATTERNS[key]
        for key in verdict.missing_sections
        if key in _schema._SECTION_ACCEPTED_PATTERNS
    }
    enriched: list[dict[str, Any]] = []
    for gate in gates:
        row = dict(gate)
        if row.get("gate") == 9 and row.get("status") == "failed" and hints:
            row["section_hints"] = hints
        enriched.append(row)
    return enriched


def derive_status(
    *,
    preflight_admitted: bool,
    schema_passed: bool,
    pinned_sha256: str | None,
    spec_sha256: str,
    has_todo_context: bool,
    gate10_failed: bool,
    gate13_failed: bool,
    skeptic: dict[str, Any],
) -> str:
    if not schema_passed:
        return "schema_failed"
    if has_todo_context and pinned_sha256 is None:
        return "not_attested"
    if has_todo_context and pinned_sha256 and pinned_sha256 != spec_sha256:
        return "drifted_since_ready"
    if has_todo_context and gate10_failed:
        return "drifted_since_ready"
    if has_todo_context and gate13_failed:
        return "skeptic_hash_missing"
    if (
        has_todo_context
        and skeptic.get("ratified")
        and skeptic.get("evidence_grounded") is False
        and not skeptic.get("deferred_to_stargate")
    ):
        return "skeptic_hash_missing"
    if preflight_admitted:
        return "pass"
    return "schema_failed"
