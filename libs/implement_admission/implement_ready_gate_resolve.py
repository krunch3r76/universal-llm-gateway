"""Assertion and dense-spec resolution helpers for implement_ready_gate."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Protocol

from implement_admission.dense_spec_schema import DENSE_SPEC_RE, spec_basename
from implement_admission.implement_ready import assertion_active
from implement_admission.scheme_resolve import (
    parse_schemed_path,
    resolve_schemed_packet_file,
)


class ImplementReadyCortex(Protocol):
    def entity_get(self, entity_id: str, **kwargs: Any) -> dict[str, Any]: ...

    def assertion_get(self, assertion_id: int) -> dict[str, Any]: ...

    def assertions(self, entity_id: str, **kwargs: Any) -> dict[str, Any]: ...


def decode_gate_attributes(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def coerce_assertion_id(raw: Any) -> int | None:
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


_IMPLEMENT_READY_STATUS = "implement_ready"
_SKEPTIC_RATIFIED_STATUS = "skeptic_ratified"


def pin_needs_resolution(
    assertion: dict[str, Any] | None,
    *,
    todo_id: str,
    now_iso: str,
) -> bool:
    if assertion is None:
        return True
    if assertion.get("entity_id") != todo_id:
        return True
    return not assertion_active(assertion, now_iso=now_iso)


def resolve_fresh_implement_ready(
    *,
    todo_id: str,
    cortex: ImplementReadyCortex,
    now_iso: str,
) -> tuple[int, dict[str, Any]] | None:
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
        aid = coerce_assertion_id(item.get("id"))
        if aid is None:
            continue
        key = (str(item.get("observed_at") or ""), aid)
        if key > best_key:
            best, best_key = item, key

    if best is None:
        return None
    return best_key[1], best


@dataclass(frozen=True, slots=True)
class SkepticRatificationOutcome:
    ratified: bool
    evidence_grounded: bool | None = None
    evidence_unresolved: list[str] | None = None
    evidence_mode: str | None = None


def resolve_skeptic_ratification(
    *,
    todo_id: str,
    cortex: ImplementReadyCortex,
    now_iso: str,
    spec_hash_uri: str | None,
    resolve_skeptic: Any | None = None,
) -> SkepticRatificationOutcome:
    if not spec_hash_uri:
        return SkepticRatificationOutcome(ratified=False)
    listed = cortex.assertions(
        todo_id,
        confidence="confirmed",
        superseded=False,
        intent="full",
        limit=50,
    )
    items = listed.get("items") if isinstance(listed, dict) else None
    if not isinstance(items, list):
        return SkepticRatificationOutcome(ratified=False)
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
        if resolve_skeptic is not None:
            outcome = resolve_skeptic(assertion=item)
            if isinstance(outcome, SkepticRatificationOutcome):
                return outcome
            if isinstance(outcome, dict):
                return SkepticRatificationOutcome(
                    ratified=True,
                    evidence_grounded=outcome.get("evidence_grounded"),
                    evidence_unresolved=outcome.get("evidence_unresolved"),
                    evidence_mode=outcome.get("evidence_mode"),
                )
        return SkepticRatificationOutcome(ratified=True)
    return SkepticRatificationOutcome(ratified=False)


def select_cited_dense_spec_uri(
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


def read_dense_spec_text(
    cited_uri: str,
    *,
    workspaces_root: Any | None = None,
) -> str | None:
    candidate = resolve_schemed_packet_file(
        cited_uri.strip(),
        workspaces_root_override=workspaces_root,
    )
    if candidate is None:
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


__all__ = [
    "ImplementReadyCortex",
    "SkepticRatificationOutcome",
    "coerce_assertion_id",
    "decode_gate_attributes",
    "pin_needs_resolution",
    "read_dense_spec_text",
    "resolve_fresh_implement_ready",
    "resolve_skeptic_ratification",
    "select_cited_dense_spec_uri",
]
