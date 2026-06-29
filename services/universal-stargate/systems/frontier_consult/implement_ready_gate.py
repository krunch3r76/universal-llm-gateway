"""Stargate dispatch adapter for todo-sourced implement-readiness admission."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from implement_admission.dense_spec_schema import (
    DENSE_SPEC_RE,
    dense_spec_hash_uri,
    spec_basename,
)
from implement_admission.implement_ready import (
    assertion_active,
    evaluate_implement_ready,
)
from implement_admission.scheme_resolve import (
    parse_schemed_path,
    resolve_schemed_packet_file,
)
from implement_admission.source_ref import parse_source_ref
from implement_admission.spec import SourceKind

from .admission import FrontierEndpointError
from .implement_admission_bridge import StargateCortexReader
from .skeptic_evidence_grounding import evaluate_skeptic_evidence_grounding


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
    if raw is None:
        return None
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str) and raw.strip().isdigit():
        return int(raw.strip())
    return None


_IMPLEMENT_READY_STATUS = "implement_ready"
_SKEPTIC_RATIFIED_STATUS = "skeptic_ratified"


def _normalize_predicate(raw: Any) -> str:
    if not isinstance(raw, str):
        return ""
    return "".join(raw.split()).lower()


def _pin_needs_resolution(
    assertion: dict[str, Any] | None,
    *,
    todo_id: str,
    now_iso: str,
) -> bool:
    """True when the pinned assertion cannot serve as the readiness record."""
    if assertion is None:
        return True
    if assertion.get("entity_id") != todo_id:
        return True
    return not assertion_active(assertion, now_iso=now_iso)


def _resolve_fresh_implement_ready(
    *,
    todo_id: str,
    cortex: StargateCortexReader,
    now_iso: str,
) -> tuple[int, dict[str, Any]] | None:
    """Latest active confirmed implement-ready assertion on the todo, or None.

    Fallback for when the pinned ``implement_ready_assertion_id`` is missing,
    inactive, or bound to the wrong entity (friction 19783): the pin is stamped
    once at first materialization and a fresh manual declaration on a reopened
    todo never restamps it. Selection keys off ``predicate_form`` ==
    ``status({todo_id}, implement_ready, current)`` so a later ``implemented``
    (or other-status) confirmed row can never be chosen — the failure mode that
    makes assertion_state.latest_confirmed-based resolution unsafe.
    """
    listed = cortex.assertions(
        todo_id,
        confidence="confirmed",
        superseded=False,
        intent="full",
        limit=50,
    )
    items = listed.get("items") if isinstance(listed, dict) else None
    if not isinstance(items, list):
        return None

    target = _normalize_predicate(
        f"status({todo_id}, {_IMPLEMENT_READY_STATUS}, current)"
    )
    best: dict[str, Any] | None = None
    best_key: tuple[str, int] = ("", -1)
    for item in items:
        if not isinstance(item, dict):
            continue
        if item.get("entity_id") != todo_id:
            continue
        if _normalize_predicate(item.get("predicate_form")) != target:
            continue
        if not assertion_active(item, now_iso=now_iso):
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


@dataclass(frozen=True, slots=True)
class _SkepticRatificationOutcome:
    ratified: bool
    evidence_grounded: bool | None = None
    evidence_unresolved: list[str] | None = None
    evidence_mode: str | None = None


def _resolve_skeptic_ratification(
    *,
    todo_id: str,
    cortex: StargateCortexReader,
    now_iso: str,
    spec_hash_uri: str | None,
    workspaces_root: Path | None = None,
) -> _SkepticRatificationOutcome:
    """True iff an active confirmed skeptic-ratification pinned to the current spec.

    Mirrors _resolve_fresh_implement_ready selection: keys off predicate_form so
    only a skeptic-ratification row (never an implemented/other-status row) counts.

    P2 hash-pin: the ratification must additionally cite the current dense-spec
    content hash (``spec_hash_uri``) in evidence_uris. Without this, a prior
    spec's still-active skeptic pass is reusable after a material spec increment —
    a stale-attestation bypass of the very gate whose job is to force a fresh
    skeptic pass for material todos (decision:recon-lifecycle-phase-review §P2).
    When the spec hash cannot be computed (unreadable spec), no ratification can
    be pinned — the evaluator already rejects unreadable specs upstream.
    """
    if not spec_hash_uri:
        return _SkepticRatificationOutcome(ratified=False)
    listed = cortex.assertions(
        todo_id,
        confidence="confirmed",
        superseded=False,
        intent="full",
        limit=50,
    )
    items = listed.get("items") if isinstance(listed, dict) else None
    if not isinstance(items, list):
        return _SkepticRatificationOutcome(ratified=False)
    target = _normalize_predicate(
        f"status({todo_id}, {_SKEPTIC_RATIFIED_STATUS}, current)"
    )
    for item in items:
        if not isinstance(item, dict):
            continue
        if item.get("entity_id") != todo_id:
            continue
        if _normalize_predicate(item.get("predicate_form")) != target:
            continue
        if not assertion_active(item, now_iso=now_iso):
            continue
        evidence = item.get("evidence_uris")
        if not (isinstance(evidence, list) and spec_hash_uri in evidence):
            continue
        outcome = evaluate_skeptic_evidence_grounding(
            reader=cortex,
            assertion=item,
            workspaces_root=workspaces_root,
        )
        return _SkepticRatificationOutcome(
            ratified=True,
            evidence_grounded=outcome.grounded,
            evidence_unresolved=outcome.unresolved,
            evidence_mode=outcome.mode,
        )
    return _SkepticRatificationOutcome(ratified=False)


def _select_cited_dense_spec_uri(
    evidence_uris: list[str] | None,
    *,
    source_uri: str | None,
) -> str | None:
    if not evidence_uris:
        return None
    cited = [u for u in evidence_uris if DENSE_SPEC_RE.search(u)]
    if not cited:
        return None
    source_base = spec_basename(source_uri or "")
    if source_base is None:
        return cited[0]
    for uri in cited:
        if spec_basename(uri) == source_base:
            return uri
    return None


def _read_dense_spec_text(
    cited_uri: str,
    *,
    workspaces_root: Path | None = None,
) -> str | None:
    candidate = resolve_schemed_packet_file(
        cited_uri.strip(),
        workspaces_root_override=workspaces_root,
    )
    if candidate is None:
        # Dense specs live in the workspaces sandbox, not the cortex store.
        # A cortex:// evidence URI routes scheme_resolve to /data/files (cortex
        # store root) where specs do not live, returning None.  Retry with the
        # bare relative path so cortex://tasks/specs/foo.md resolves the same
        # file as workspaces://tasks/specs/foo.md (friction 21175).
        parsed = parse_schemed_path(cited_uri.strip())
        if parsed.scheme == "cortex" and parsed.rel_path:
            candidate = resolve_schemed_packet_file(
                parsed.rel_path,
                workspaces_root_override=workspaces_root,
            )
    if candidate is None:
        return None
    try:
        return candidate.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def require_implement_ready(
    *,
    request_id: str,
    source_ref: str | None,
    cortex: StargateCortexReader,
) -> None:
    """Hard gate for todo-sourced implement dispatch. No-op for non-todo sources."""
    if source_ref is None:
        return

    ref = parse_source_ref(source_ref)
    if ref.source_kind != SourceKind.TODO.value:
        return

    entity = cortex.entity_get(ref.canonical_ref)
    attrs = _decode_attributes(entity.get("attributes"))
    triage = (attrs.get("density_triage") or "").strip()
    now_iso = datetime.now(UTC).isoformat()

    aid = _coerce_assertion_id(attrs.get("implement_ready_assertion_id"))
    assertion: dict[str, Any] | None = None
    if aid is not None:
        loaded = cortex.assertion_get(aid)
        if isinstance(loaded, dict) and "error" not in loaded:
            assertion = loaded

    # Friction 19783: the pinned implement_ready_assertion_id is stamped once at
    # first materialization (distill) and is never re-resolved here. When a todo
    # is reopened for a new spec version, a fresh implement-ready declaration
    # does not restamp the pin, so the gate keeps citing the superseded id and
    # the documented "record a fresh implement-ready declaration" remedy is inert
    # unless distillation is rerun. Fall back to the latest active confirmed
    # implement-ready assertion on the todo when the pinned row is missing,
    # inactive, or bound to the wrong entity. Only the judgment_required lane
    # consults an assertion at all. Resolution lives in the adapter;
    # evaluate_implement_ready stays a pure verdict over a single resolved row.
    if triage == "judgment_required" and _pin_needs_resolution(
        assertion, todo_id=ref.canonical_ref, now_iso=now_iso
    ):
        resolved = _resolve_fresh_implement_ready(
            todo_id=ref.canonical_ref, cortex=cortex, now_iso=now_iso
        )
        if resolved is not None:
            aid, assertion = resolved

    evidence = assertion.get("evidence_uris") if assertion else None
    cited_uri: str | None = None
    dense_spec_text: str | None = None
    if isinstance(evidence, list):
        cited_uri = _select_cited_dense_spec_uri(
            evidence, source_uri=entity.get("source_uri")
        )
        if cited_uri is not None:
            dense_spec_text = _read_dense_spec_text(cited_uri)

    raw_files = attrs.get("files_expected")
    files_expected = raw_files if isinstance(raw_files, list) else []
    raw_acs = attrs.get("acceptance_criteria")
    acceptance_criteria = raw_acs if isinstance(raw_acs, list) else []

    spec_hash_uri = dense_spec_hash_uri(dense_spec_text) if dense_spec_text else None
    skeptic_outcome = _SkepticRatificationOutcome(ratified=False)
    if triage == "judgment_required":
        skeptic_outcome = _resolve_skeptic_ratification(
            todo_id=ref.canonical_ref,
            cortex=cortex,
            now_iso=now_iso,
            spec_hash_uri=spec_hash_uri,
        )

    # P2: recon_waived must be a non-empty string reason, not a bare truthy flag —
    # a documented waiver (recon-default rule: recon_waived="<reason>"), so a
    # casually-written boolean cannot silently defeat the material skeptic gate.
    raw_waived = attrs.get("recon_waived")
    recon_waived = isinstance(raw_waived, str) and bool(raw_waived.strip())

    verdict = evaluate_implement_ready(
        todo_id=ref.canonical_ref,
        density_triage=attrs.get("density_triage"),
        source_uri=entity.get("source_uri"),
        implement_ready_assertion_id=aid,
        assertion=assertion,
        now_iso=now_iso,
        dense_spec_uri=cited_uri,
        dense_spec_text=dense_spec_text,
        files_expected=files_expected,
        acceptance_criteria=acceptance_criteria,
        entity_name=entity.get("name"),
        skeptic_ratified=skeptic_outcome.ratified,
        recon_waived=recon_waived,
        skeptic_evidence_grounded=skeptic_outcome.evidence_grounded,
        skeptic_evidence_unresolved=skeptic_outcome.evidence_unresolved,
        skeptic_evidence_mode=skeptic_outcome.evidence_mode,
    )
    if not verdict.admitted:
        raise FrontierEndpointError(
            request_id=request_id,
            field="source_ref",
            reason=verdict.reason or verdict.code or "implement_not_ready",
            status_code=422,
            code=verdict.code or "implement_not_ready",
        )


__all__ = ["require_implement_ready"]
