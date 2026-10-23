"""Map normalized cortex.life/v1 patches to existing cortex op plans."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException

from ..db import query
from ..entity_aliases import resolve_entity_reference
from ..entity_crud import list_entities_impl
from .registry import LifeVocabRegistry

_METADATA_KEYS = frozenset(
    {"@context", "@graph", "@version", "@vocab", "@id", "name", "description"}
)
_TYPED_ENTITY_ATTRS = frozenset({"name", "description", "aliases", "attributes"})


@dataclass(frozen=True)
class OpPlanEntry:
    op: str
    args: dict[str, Any]
    resolves: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"op": self.op, "args": self.args}
        if self.resolves:
            out["resolves"] = self.resolves
        return out


@dataclass(frozen=True)
class CandidateMatch:
    statement_idx: int
    field: str
    input_ref: str
    matches: list[dict[str, str]]

    def as_dict(self) -> dict[str, Any]:
        return {
            "statement_idx": self.statement_idx,
            "field": self.field,
            "input_ref": self.input_ref,
            "matches": self.matches,
        }


def _iter_statements(patch: dict[str, Any]) -> list[dict[str, Any]]:
    graph = patch.get("@graph")
    if graph is not None:
        return list(graph)
    return [patch]


def _object_ref(value: object) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, dict) and isinstance(value.get("@id"), str):
        return value["@id"]
    return None


def _surface_form_matches(conn: sqlite3.Connection, mention: str) -> list[dict[str, str]]:
    rows = query(
        conn,
        "SELECT DISTINCT entity_id FROM surface_forms WHERE mention = ? ORDER BY entity_id",
        (mention,),
    )
    return [{"entity_id": str(r["entity_id"])} for r in rows]


def _search_matches(conn: sqlite3.Connection, mention: str) -> list[dict[str, str]]:
    result = list_entities_impl(conn, query=mention, limit=10)
    return [
        {"entity_id": str(item["id"]), "name": str(item.get("name") or "")}
        for item in result.get("items", [])
    ]


def _alias_matches(conn: sqlite3.Connection, mention: str) -> list[dict[str, str]]:
    rows = query(
        conn,
        "SELECT entity_id, entity_type, alias FROM entity_aliases "
        "WHERE alias = ? ORDER BY entity_id",
        (mention,),
    )
    return [
        {
            "entity_id": str(row["entity_id"]),
            "entity_type": str(row["entity_type"]),
            "alias": str(row["alias"]),
        }
        for row in rows
    ]


def _dedupe_matches(matches: list[dict[str, str]]) -> list[dict[str, str]]:
    seen: set[str] = set()
    out: list[dict[str, str]] = []
    for match in matches:
        eid = match.get("entity_id", "")
        if not eid or eid in seen:
            continue
        seen.add(eid)
        out.append(match)
    return out


def _bare_ref_matches(conn: sqlite3.Connection, ref: str) -> list[dict[str, str]]:
    """Collect candidate matches for refs without a type prefix (D2)."""
    return _dedupe_matches(
        _surface_form_matches(conn, ref)
        + _alias_matches(conn, ref)
        + _search_matches(conn, ref)
    )


def resolve_entity_id(
    conn: sqlite3.Connection,
    ref: str,
    *,
    statement_idx: int,
    field: str,
    planned_ids: frozenset[str] | None = None,
) -> tuple[str | None, CandidateMatch | None, dict[str, Any] | None]:
    """Resolve ref to canonical id, or return ambiguity candidate."""
    if planned_ids and ref in planned_ids:
        return ref, None, {"input": ref, "entity_id": ref, "via": "planned_create"}

    if ":" not in ref:
        return (
            None,
            CandidateMatch(statement_idx, field, ref, _bare_ref_matches(conn, ref)),
            None,
        )

    rows = query(conn, "SELECT id FROM entities WHERE id = ?", (ref,))
    if rows:
        return str(rows[0]["id"]), None, None

    try:
        resolved = resolve_entity_reference(conn, ref, resolve_aliases=True)
        note = resolved.resolved_alias or {"input": ref, "entity_id": resolved.entity_id}
        return resolved.entity_id, None, note
    except HTTPException as exc:
        if exc.status_code == 400 and isinstance(exc.detail, dict):
            matches = [
                {
                    "entity_id": str(m.get("entity_id", "")),
                    "entity_type": str(m.get("entity_type", "")),
                    "alias": str(m.get("alias", "")),
                }
                for m in exc.detail.get("matches", [])
            ]
            return (
                None,
                CandidateMatch(statement_idx, field, ref, matches),
                None,
            )

    sf = _surface_form_matches(conn, ref)
    if len(sf) == 1:
        eid = sf[0]["entity_id"]
        return eid, None, {"input": ref, "entity_id": eid, "via": "surface_forms"}
    if len(sf) > 1:
        return (
            None,
            CandidateMatch(statement_idx, field, ref, sf),
            None,
        )

    search = _search_matches(conn, ref)
    if len(search) == 1:
        eid = search[0]["entity_id"]
        return eid, None, {"input": ref, "entity_id": eid, "via": "entity_search"}
    if len(search) > 1:
        return (
            None,
            CandidateMatch(statement_idx, field, ref, search),
            None,
        )

    return None, None, None


def _typing_args(stmt: dict[str, Any], subject: str) -> dict[str, Any]:
    entity_type = stmt.get("@type") or stmt.get("a")
    args: dict[str, Any] = {"id": subject, "type": entity_type}
    for key in _TYPED_ENTITY_ATTRS:
        if key in stmt:
            args[key] = stmt[key]
    return args


def build_op_plan(
    patch: dict[str, Any],
    registry: LifeVocabRegistry,
    conn: sqlite3.Connection,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return (op_plan, candidates). Skips statements with unresolved ambiguity."""
    plan: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    planned_ids: set[str] = set()

    for idx, stmt in enumerate(_iter_statements(patch)):
        subject = stmt["@id"]
        typing_key = "@type" if "@type" in stmt else ("a" if "a" in stmt else None)
        planned = frozenset(planned_ids)

        if typing_key:
            plan.append(
                OpPlanEntry(
                    op="entity_create",
                    args=_typing_args(stmt, subject),
                ).as_dict()
            )
            planned_ids.add(subject)

        for key, value in stmt.items():
            if key in _METADATA_KEYS or key.startswith("@") or key in {"@type", "a"}:
                continue
            if typing_key and key in _TYPED_ENTITY_ATTRS:
                continue
            spec = registry.predicate_for_key(key)
            if spec is None:
                continue

            subj_id, subj_cand, subj_note = resolve_entity_id(
                conn,
                subject,
                statement_idx=idx,
                field="subject",
                planned_ids=planned,
            )
            if subj_cand:
                candidates.append(subj_cand.as_dict())
                continue
            if subj_id is None:
                candidates.append(
                    CandidateMatch(
                        idx,
                        "subject",
                        subject,
                        [],
                    ).as_dict()
                )
                continue

            if spec.klass == "relationship":
                obj_ref = _object_ref(value)
                if obj_ref is None:
                    continue
                tgt_id, tgt_cand, tgt_note = resolve_entity_id(
                    conn,
                    obj_ref,
                    statement_idx=idx,
                    field="object",
                    planned_ids=planned,
                )
                if tgt_cand:
                    candidates.append(tgt_cand.as_dict())
                    continue
                if tgt_id is None:
                    candidates.append(
                        CandidateMatch(idx, "object", obj_ref, []).as_dict()
                    )
                    continue
                resolves = {}
                if subj_note:
                    resolves["subject"] = subj_note
                if tgt_note:
                    resolves["object"] = tgt_note
                plan.append(
                    OpPlanEntry(
                        op="relationship_create",
                        args={
                            "source_id": subj_id,
                            "target_id": tgt_id,
                            "type_id": spec.name,
                        },
                        resolves=resolves or None,
                    ).as_dict()
                )

            elif spec.klass == "literal" and spec.cortex_op == "assert":
                plan.append(
                    OpPlanEntry(
                        op="assert",
                        args={
                            "entity_id": subj_id,
                            "claim": value,
                            "confidence": "believed",
                            "evidence": "operator-stated via imprint",
                            "derivation_type": "user_statement",
                            "confidence_score": 0.9,
                        },
                        resolves={"subject": subj_note} if subj_note else None,
                    ).as_dict()
                )

            elif spec.klass == "literal" and spec.cortex_op == "entity_update":
                attr = spec.allowlisted_attribute or key
                plan.append(
                    OpPlanEntry(
                        op="entity_update",
                        args={
                            "entity_id": subj_id,
                            "attributes": {attr: value},
                        },
                        resolves={"subject": subj_note} if subj_note else None,
                    ).as_dict()
                )

    return plan, candidates


def normalize_patch(patch: dict[str, Any], registry: LifeVocabRegistry) -> dict[str, Any]:
    """Ensure @context and @graph envelope for response contract."""
    normalized: dict[str, Any] = {"@context": registry.context_id}
    if "@graph" in patch:
        normalized["@graph"] = patch["@graph"]
    else:
        node = {k: v for k, v in patch.items() if k != "@context"}
        normalized["@graph"] = [node]
    return normalized
