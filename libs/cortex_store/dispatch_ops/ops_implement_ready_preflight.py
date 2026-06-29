"""Cortex dispatch op: implement_ready_preflight."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from implement_admission.dense_spec_schema import DENSE_SPEC_RE, dense_spec_hash_uri, spec_basename
from implement_admission.gate_distillation import read_dense_spec_text
from implement_admission.implement_ready_preflight import preflight_implement_ready
from implement_admission.source_ref import parse_source_ref
from implement_admission.spec import SourceKind

from .ops_assertions import _op_assertion_get, _op_assertions
from .ops_entities import _op_entity_get


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
    cited = [u for u in evidence if DENSE_SPEC_RE.search(u)]
    if not cited:
        return None
    source_base = spec_basename(source_uri or "")
    if source_base is None:
        return cited[0]
    for uri in cited:
        if spec_basename(uri) == source_base:
            return uri
    return cited[0]


def _resolve_skeptic_outcome(
    *,
    todo_id: str,
    spec_hash_uri: str | None,
    now_iso: str,
) -> bool:
    """Check for active confirmed skeptic-ratified assertion citing current spec sha."""
    if not spec_hash_uri:
        return False

    listed = _op_assertions(
        entity_id=todo_id,
        confidence="confirmed",
        superseded=False,
        intent="full",
        limit=50,
    )
    items = listed.get("items") if isinstance(listed, dict) else None
    if not isinstance(items, list):
        return False

    target_pf = _normalize_predicate(f"status({todo_id}, skeptic_ratified, current)")
    for item in items:
        if not isinstance(item, dict):
            continue
        if item.get("entity_id") != todo_id:
            continue
        if _assertion_is_inactive(item, now_iso):
            continue
        pf = item.get("predicate_form") or ""
        by_pf = _normalize_predicate(pf) == target_pf
        claim_prefix = _normalize_predicate((item.get("claim") or "")[:90])
        by_claim = claim_prefix.startswith(target_pf)
        if not (by_pf or by_claim):
            continue
        evidence = item.get("evidence_uris")
        if isinstance(evidence, list) and spec_hash_uri in evidence:
            return True
    return False


def _spec_path_from_uri(uri: str) -> str | None:
    match = DENSE_SPEC_RE.search(uri)
    return match.group(0) if match else None


def _op_implement_ready_preflight(
    source_ref: str | None = None,
    **_: object,
) -> dict[str, Any]:
    """Non-writing preflight for todo-sourced implement dispatch."""
    if not source_ref:
        return {"ok": True, "admitted": True, "note": "source_ref is required; pass todo:{slug}"}

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

    entity = _op_entity_get(entity_id=todo_id)
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

    spec_hash_uri = dense_spec_hash_uri(spec_text) if spec_text else None
    skeptic_ratified = False
    if triage == "judgment_required":
        skeptic_ratified = _resolve_skeptic_outcome(
            todo_id=todo_id,
            spec_hash_uri=spec_hash_uri,
            now_iso=now_iso,
        )

    raw_waived = attrs.get("recon_waived")
    recon_waived = isinstance(raw_waived, str) and bool(raw_waived.strip())

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
        skeptic_ratified=skeptic_ratified,
        recon_waived=recon_waived,
    )
    return report.to_dict()


__all__ = ["_op_implement_ready_preflight"]
