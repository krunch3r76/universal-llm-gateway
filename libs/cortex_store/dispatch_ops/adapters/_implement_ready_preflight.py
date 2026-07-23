"""Cortex dispatch op: implement_ready_preflight."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from implement_admission.dense_spec_schema import (
    DENSE_SPEC_RE,
    dense_spec_hash_uri,
    validate_dense_spec,
)
from implement_admission.density_triage_gate import check_requested_bool
from implement_admission.gate_distillation import read_dense_spec_text
from implement_admission.implement_ready_gate6_resolve import resolve_gate6_ratification
from implement_admission.implement_ready_gate_resolve import (
    SkepticRatificationOutcome,
    resolve_skeptic_ratification,
)
from implement_admission.implement_ready_preflight import (
    GateStatus,
    preflight_implement_ready,
)
from implement_admission.recon_waiver import resolve_effective_recon_waived
from implement_admission.source_ref import parse_source_ref
from implement_admission.spec import SourceKind

from ...event_publisher import cortex_implement_recon_waived
from ..ops_assertions import _op_assertion_get, _op_assertions
from ..ops_entities import _op_entity_get


def _decode_attributes(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _coerce_assertion_id(raw: Any) -> int | None:
    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str) and raw.strip().isdigit():
        return int(raw.strip())
    return None


def _normalize_predicate(raw: Any) -> str:
    if not isinstance(raw, str):
        return ""
    return "".join(raw.split()).lower()


def _assertion_is_inactive(assertion: dict[str, Any], now_iso: str) -> bool:
    if assertion.get("superseded_by") is not None:
        return True
    valid_until = assertion.get("valid_until")
    return bool(valid_until and str(valid_until) <= now_iso)


def _pin_needs_resolution(
    assertion: dict[str, Any] | None,
    *,
    todo_id: str,
    now_iso: str,
) -> bool:
    if assertion is None:
        return True
    if assertion.get("entity_id") != todo_id:
        return True
    return _assertion_is_inactive(assertion, now_iso)


def _resolve_fresh_assertion(
    *,
    todo_id: str,
    now_iso: str,
) -> tuple[int, dict[str, Any]] | None:
    listed = _op_assertions(
        entity_id=todo_id,
        confidence="confirmed",
        superseded=False,
        intent="full",
        limit=50,
    )
    items = listed.get("items") if isinstance(listed, dict) else None
    if not isinstance(items, list):
        return None

    target = _normalize_predicate(f"status({todo_id}, implement_ready, current)")
    best: dict[str, Any] | None = None
    best_key: tuple[str, int] = ("", -1)
    for item in items:
        if not isinstance(item, dict):
            continue
        if item.get("entity_id") != todo_id:
            continue
        if _normalize_predicate(item.get("predicate_form")) != target:
            continue
        if _assertion_is_inactive(item, now_iso):
            continue
        aid = _coerce_assertion_id(item.get("id"))
        if aid is None:
            continue
        key = (str(item.get("observed_at") or ""), aid)
        if key > best_key:
            best, best_key = item, key

    if best is None:
        return None
    return best_key[1], best


def _select_cited_spec_uri(
    evidence: list[str],
    *,
    source_uri: str | None,
) -> str | None:
    from implement_admission.implement_ready_gate_resolve import (
        select_cited_dense_spec_uri,
    )

    return select_cited_dense_spec_uri(evidence, source_uri=source_uri)


class _CortexOpsShim:
    """ImplementReadyCortex over the in-process dispatch ops."""

    def entity_get(self, entity_id: str, **kwargs: Any) -> dict[str, Any]:
        return _op_entity_get(entity_id=entity_id, **kwargs)

    def assertion_get(self, assertion_id: int) -> dict[str, Any]:
        return _op_assertion_get(assertion_id=assertion_id)

    def assertions(self, entity_id: str, **kwargs: Any) -> dict[str, Any]:
        return _op_assertions(entity_id=entity_id, **kwargs)


def _resolve_skeptic_ratification_outcome(
    *,
    todo_id: str,
    spec_hash_uri: str | None,
    now_iso: str,
) -> SkepticRatificationOutcome:
    """Shared gate-13 resolver (with unmet-subcondition reason enumeration).

    ``match_claim_prefix=True`` preserves this adapter's historical
    claim-prefix fallback for ratifications whose predicate_form was dropped
    by supersede (assertion 21699).
    """
    return resolve_skeptic_ratification(
        todo_id=todo_id,
        cortex=_CortexOpsShim(),
        now_iso=now_iso,
        spec_hash_uri=spec_hash_uri,
        match_claim_prefix=True,
    )


def _resolve_skeptic_outcome(
    *,
    todo_id: str,
    spec_hash_uri: str | None,
    now_iso: str,
) -> bool:
    """Boolean view of the skeptic ratification (doc_validate support callers)."""
    return _resolve_skeptic_ratification_outcome(
        todo_id=todo_id,
        spec_hash_uri=spec_hash_uri,
        now_iso=now_iso,
    ).ratified


def _spec_path_from_uri(uri: str) -> str | None:
    """Return a readable dense-spec path, preserving Share URI scheme.

    Historically this returned only the ``DENSE_SPEC_RE`` capture (bare
    ``notes/system/specs/...``), which dropped ``cortex://`` and forced the
    admission reader to guess the sandbox. Prefer the original URI when it
    already carries a scheme; otherwise return the bare capture (resolver
    infers cortex for CORTEX_FILE_ROOT prefixes — friction 23230).
    """
    match = DENSE_SPEC_RE.search(uri)
    if not match:
        return None
    stripped = uri.strip()
    if "://" in stripped or stripped.lower().startswith(
        ("cortex:", "workspaces:", "ws:", "files:")
    ):
        return stripped
    return match.group(0)


def _op_implement_ready_preflight(
    source_ref: str | None = None,
    **_: object,
) -> dict[str, Any]:
    """Non-writing preflight for todo-sourced implement dispatch."""
    if not source_ref:
        return {
            "ok": True,
            "admitted": True,
            "note": "source_ref is required; pass todo:{slug}",
        }

    ref = parse_source_ref(source_ref)
    if ref.source_kind != SourceKind.TODO.value:
        return {
            "ok": True,
            "admitted": True,
            "note": (
                f"non-todo source_ref ({ref.source_kind!r}); "
                "implement-ready gate does not apply"
            ),
        }

    todo_id = ref.canonical_ref
    now_iso = datetime.now(UTC).isoformat()

    entity = _op_entity_get(entity_id=todo_id, intent="full")
    if not entity or "error" in entity:
        return {"ok": False, "admitted": False, "error": f"todo not found: {todo_id}"}

    attrs = _decode_attributes(entity.get("attributes"))
    triage = (attrs.get("density_triage") or "").strip() or None
    aid = _coerce_assertion_id(attrs.get("implement_ready_assertion_id"))

    assertion: dict[str, Any] | None = None
    if aid is not None:
        loaded = _op_assertion_get(assertion_id=aid)
        if isinstance(loaded, dict) and "error" not in loaded:
            assertion = loaded

    resolution: dict[str, Any] | None = None
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
        elif aid is not None:
            resolution = {
                "pinned_assertion_id": aid,
                "effective_assertion_id": aid,
                "used_fresh_assertion_fallback": False,
            }

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

    consult_thread = str(attrs.get("consult_thread") or "").strip() or None
    consult_verdict = str(attrs.get("verdict") or attrs.get("consult_verdict") or "").strip() or None
    consultant_family = str(attrs.get("consultant_family") or "").strip() or None
    consultant_substrate = str(attrs.get("consultant_substrate") or "").strip() or None

    spec_hash_uri = dense_spec_hash_uri(spec_text) if spec_text else None

    raw_waived = attrs.get("recon_waived")
    recon_waived, recon_waiver, stale_discarded = resolve_effective_recon_waived(
        raw_waived,
        spec_hash_uri,
    )
    if stale_discarded and recon_waiver is not None:
        cortex_implement_recon_waived(
            todo_id=todo_id,
            stale=True,
            stale_reason="spec_sha256_mismatch",
            **recon_waiver.event_payload(),
        )

    skeptic_outcome = SkepticRatificationOutcome(ratified=False)
    check_requested = check_requested_bool(attrs.get("check_requested"))
    if triage == "judgment_required" and check_requested:
        skeptic_outcome = _resolve_skeptic_ratification_outcome(
            todo_id=todo_id,
            spec_hash_uri=spec_hash_uri,
            now_iso=now_iso,
        )
        if (
            skeptic_outcome.assertion is None
            and not skeptic_outcome.ratified
            and not recon_waived
            and assertion is not None
        ):
            try:
                from implement_admission.closeout_helpers import workspaces_root

                from .._doc_validate_skeptic import DispatchSkepticBusReader

                reader = DispatchSkepticBusReader()
                fetch_bus_turn = reader.bus_turn_get
                ws_root = workspaces_root()
            except Exception:
                fetch_bus_turn = None
                ws_root = None
            gate6_outcome = resolve_gate6_ratification(
                todo_attrs=attrs,
                implement_ready_assertion=assertion,
                spec_hash_uri=spec_hash_uri,
                fetch_bus_turn=fetch_bus_turn,
                workspaces_root=ws_root,
            )
            if gate6_outcome.ratified:
                skeptic_outcome = gate6_outcome

    # Dispatch-parity evidence grounding (friction 22906): evaluate the same
    # FILE_EVIDENCE_PATHS sub-checks the implement dispatch enforces where the
    # skeptic bus turn is fetchable; the lib falls back to an explicit warning
    # when grounding stays deferred.
    evidence_grounded: bool | None = None
    evidence_unresolved: list[str] | None = None
    evidence_mode: str | None = None
    if check_requested and skeptic_outcome.ratified and not recon_waived:
        if skeptic_outcome.evidence_grounded is not None:
            evidence_grounded = skeptic_outcome.evidence_grounded
            evidence_unresolved = skeptic_outcome.evidence_unresolved
            evidence_mode = skeptic_outcome.evidence_mode
        elif skeptic_outcome.assertion is not None:
            try:
                from implement_admission.closeout_helpers import workspaces_root

                from .._doc_validate_skeptic import evaluate_skeptic_grounding

                grounding = evaluate_skeptic_grounding(
                    skeptic_assertion=skeptic_outcome.assertion,
                    ws_root=workspaces_root(),
                )
            except Exception:
                grounding = {"deferred_to_stargate": True}
            if not grounding.get("deferred_to_stargate"):
                evidence_grounded = grounding.get("evidence_grounded")
                evidence_unresolved = grounding.get("evidence_unresolved")
                evidence_mode = grounding.get("evidence_mode")

    report = preflight_implement_ready(
        todo_id=todo_id,
        density_triage=attrs.get("density_triage"),
        source_uri=entity.get("source_uri"),
        implement_ready_assertion_id=aid,
        assertion=assertion,
        now_iso=now_iso,
        dense_spec_uri=cited_uri,
        dense_spec_text=spec_text,
        files_expected=files_expected,
        acceptance_criteria=acceptance_criteria,
        entity_name=entity.get("name"),
        resolution=resolution,
        skeptic_ratified=skeptic_outcome.ratified,
        recon_waived=recon_waived,
        check_requested=check_requested,
        recon_waiver=recon_waiver.to_gate_sibling() if recon_waiver else None,
        skeptic_evidence_grounded=evidence_grounded,
        skeptic_evidence_unresolved=evidence_unresolved,
        skeptic_evidence_mode=evidence_mode,
        skeptic_unratified_reason=skeptic_outcome.reason,
        consult_thread=consult_thread,
        verdict=consult_verdict,
        consultant_family=consultant_family,
        consultant_substrate=consultant_substrate,
    )
    result = report.to_dict()
    gate9 = next((g for g in report.gates if g.gate == 9), None)
    if (
        gate9 is not None
        and gate9.status == GateStatus.FAILED
        and spec_text is not None
    ):
        schema = validate_dense_spec(spec_text)
        if schema.missing_sections:
            enriched = dict(result.get("resolution") or {})
            enriched["missing_sections"] = list(schema.missing_sections)
            enriched["doc_template_hint"] = (
                "cortex(doc_template, doc_type=implement_dense_spec)"
            )
            result["resolution"] = enriched
    return result


__all__ = ["_op_implement_ready_preflight"]
