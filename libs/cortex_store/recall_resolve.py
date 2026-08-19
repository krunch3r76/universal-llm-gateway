"""Seed and query resolution for life-recall G1 — no proposal store."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from .life_imprint.op_plan import _bare_ref_matches, resolve_entity_id
from .recall_models import RecallCandidate, ResolvedEntity
from .routes.assertions._search import _search_assertions_impl

_SEARCH_SEED_CAP = 5


@dataclass(frozen=True)
class ResolveOutcome:
    """Resolution result before card assembly."""

    resolved: list[ResolvedEntity]
    candidates: list[RecallCandidate]
    resolver_miss: bool


def _match_name(conn: sqlite3.Connection, entity_id: str) -> str | None:
    from .db import query

    rows = query(conn, "SELECT name FROM entities WHERE id = ?", (entity_id,))
    if not rows:
        return None
    name = rows[0].get("name")
    return str(name) if name else None


def _candidate_from_matches(
    ref: str,
    matches: list[dict[str, str]],
) -> list[RecallCandidate]:
    out: list[RecallCandidate] = []
    for match in matches:
        eid = match.get("entity_id", "")
        if not eid:
            continue
        why = ref
        if match.get("alias"):
            why = f"alias:{match['alias']}"
        elif match.get("name"):
            why = f"search:{match['name']}"
        out.append(
            RecallCandidate(
                entity_id=eid,
                name=match.get("name"),
                why_matched=why,
            )
        )
    return out


def _resolved_from_note(
    entity_id: str,
    note: dict[str, object] | None,
) -> ResolvedEntity:
    via = "typed_id"
    confidence: str | None = None
    if note:
        via = str(note.get("via") or via)
        if note.get("confidence") is not None:
            confidence = str(note["confidence"])
    return ResolvedEntity(entity_id=entity_id, via=via, confidence=confidence)


def _resolve_typed_ref(
    conn: sqlite3.Connection,
    ref: str,
    *,
    field: str,
) -> tuple[list[ResolvedEntity], list[RecallCandidate]]:
    eid, cand, note = resolve_entity_id(
        conn,
        ref,
        statement_idx=0,
        field=field,
    )
    if cand is not None:
        return [], _candidate_from_matches(ref, cand.matches)
    if eid:
        return [_resolved_from_note(eid, note)], []
    return [], []


def _resolve_bare_q(conn: sqlite3.Connection, q: str) -> ResolveOutcome:
    matches = _bare_ref_matches(conn, q)
    if len(matches) == 1:
        eid = matches[0]["entity_id"]
        return ResolveOutcome(
            resolved=[ResolvedEntity(entity_id=eid, via="bare_ref")],
            candidates=[],
            resolver_miss=False,
        )
    if len(matches) > 1:
        return ResolveOutcome(
            resolved=[],
            candidates=_candidate_from_matches(q, matches),
            resolver_miss=False,
        )

    search_ids = _search_seed_entity_ids(conn, q)
    if len(search_ids) == 1:
        return ResolveOutcome(
            resolved=[ResolvedEntity(entity_id=search_ids[0], via="search_seeder")],
            candidates=[],
            resolver_miss=False,
        )
    if len(search_ids) > 1:
        search_matches = [
            {"entity_id": eid, "name": _match_name(conn, eid) or ""} for eid in search_ids
        ]
        return ResolveOutcome(
            resolved=[],
            candidates=_candidate_from_matches(q, search_matches),
            resolver_miss=False,
        )
    return ResolveOutcome(resolved=[], candidates=[], resolver_miss=True)


def _search_seed_entity_ids(conn: sqlite3.Connection, q: str) -> list[str]:
    result = _search_assertions_impl(
        q=q,
        superseded=False,
        entity_type=None,
        limit=_SEARCH_SEED_CAP,
        intent="summary",
        include_compaction_pointers=False,
    )
    seen: set[str] = set()
    ids: list[str] = []
    for item in result.items:
        eid = item.get("entity_id") if isinstance(item, dict) else getattr(item, "entity_id", None)
        if not eid:
            continue
        eid_str = str(eid)
        if eid_str in seen:
            continue
        seen.add(eid_str)
        ids.append(eid_str)
        if len(ids) >= _SEARCH_SEED_CAP:
            break
    return ids


def resolve_recall_inputs(
    conn: sqlite3.Connection,
    *,
    q: str | None,
    seeds: list[str] | None,
) -> ResolveOutcome:
    """Resolve optional q and seeds into resolved hubs, candidates, or miss."""
    all_resolved: list[ResolvedEntity] = []
    all_candidates: list[RecallCandidate] = []
    saw_input = False

    for seed in seeds or []:
        seed = seed.strip()
        if not seed:
            continue
        saw_input = True
        resolved, candidates = _resolve_typed_ref(conn, seed, field="seed")
        if candidates:
            all_candidates.extend(candidates)
            continue
        all_resolved.extend(resolved)

    if q and q.strip():
        saw_input = True
        q = q.strip()
        if ":" in q:
            resolved, candidates = _resolve_typed_ref(conn, q, field="q")
            if candidates:
                all_candidates.extend(candidates)
            else:
                all_resolved.extend(resolved)
        else:
            bare = _resolve_bare_q(conn, q)
            if bare.candidates:
                all_candidates.extend(bare.candidates)
            elif bare.resolved:
                all_resolved.extend(bare.resolved)
            elif bare.resolver_miss and not all_resolved:
                return ResolveOutcome(resolved=[], candidates=[], resolver_miss=True)

    if all_candidates:
        return ResolveOutcome(resolved=[], candidates=all_candidates, resolver_miss=False)

    deduped: list[ResolvedEntity] = []
    seen_ids: set[str] = set()
    for item in all_resolved:
        if item.entity_id in seen_ids:
            continue
        seen_ids.add(item.entity_id)
        deduped.append(item)

    if deduped:
        return ResolveOutcome(resolved=deduped, candidates=[], resolver_miss=False)

    if saw_input:
        return ResolveOutcome(resolved=[], candidates=[], resolver_miss=True)
    return ResolveOutcome(resolved=[], candidates=[], resolver_miss=True)
