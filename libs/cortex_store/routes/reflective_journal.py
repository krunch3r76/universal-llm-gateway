"""Reflective journal — first-person agent introspection substrate.

Distinct from session_journals (work tracking). Entries are written in
the agent's own register/voice. Consolidation entries synthesize raw
entries with anti-coherence-theater safeguards (tension_points,
contradiction_set, falsifier).
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, HTTPException, Query, status
from universal_logging import get_logger

from ..db import cortex_conn, json_decode, query
from ..models import (
    JournalLinkCreate,
    JournalLinkItem,
    ReflectiveEntryCreate,
    ReflectiveEntryItem,
    ReflectiveEntryList,
)

logger = get_logger("cortex-api.reflective_journal")
router = APIRouter(prefix="/reflective-journal", tags=["reflective-journal"])

_VALID_KINDS = frozenset({"entry", "reflection", "revision", "consolidation"})
_VALID_LINK_TYPES = frozenset(
    {
        "contradicts",
        "refines",
        "supersedes",
        "reopens",
        "unresolved_with",
        "continues",
        "related",
        "handoff_for",
    }
)


def _insert_reflective_entry_tx(
    conn: object,
    *,
    agent: str,
    register: str,
    entry: str,
    kind: str,
    session_id: str | None = None,
    revises: int | None = None,
    consolidation_data_json: str | None = None,
) -> int:
    """Insert a reflective journal row on an existing transaction."""
    if kind not in _VALID_KINDS:
        raise ValueError(
            f"Invalid kind {kind!r}. Must be one of: {sorted(_VALID_KINDS)}"
        )
    if kind == "revision" and revises is None:
        raise ValueError("revises is required for kind='revision'")
    if kind == "consolidation" and consolidation_data_json is None:
        raise ValueError("consolidation_data_json is required for kind='consolidation'")
    if kind != "consolidation" and consolidation_data_json is not None:
        raise ValueError(
            "consolidation_data_json is only valid for kind='consolidation'"
        )
    if revises is not None:
        exists = query(
            conn,  # type: ignore[arg-type]
            "SELECT 1 FROM reflective_journal WHERE id = ?",
            (revises,),
        )
        if not exists:
            raise ValueError(f"revises target {revises} not found")
    cur = conn.execute(  # type: ignore[union-attr]
        "INSERT INTO reflective_journal "
        "(agent, register, entry, kind, session_id, revises, consolidation_data) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (agent, register, entry, kind, session_id, revises, consolidation_data_json),
    )
    entry_id = cur.lastrowid
    assert entry_id is not None
    return int(entry_id)


def _insert_journal_link_tx(
    conn: object,
    *,
    from_entry: int,
    to_entry: int | None = None,
    to_entity: str | None = None,
    link_type: str,
) -> int:
    """Insert a journal link on an existing transaction."""
    if to_entry is None and to_entity is None:
        raise ValueError("Either to_entry or to_entity is required")
    if link_type not in _VALID_LINK_TYPES:
        raise ValueError(
            f"Invalid link_type {link_type!r}. Must be one of: {sorted(_VALID_LINK_TYPES)}"
        )
    exists = query(
        conn,  # type: ignore[arg-type]
        "SELECT 1 FROM reflective_journal WHERE id = ?",
        (from_entry,),
    )
    if not exists:
        raise ValueError(f"Entry {from_entry} not found")
    if to_entry is not None:
        target_exists = query(
            conn,  # type: ignore[arg-type]
            "SELECT 1 FROM reflective_journal WHERE id = ?",
            (to_entry,),
        )
        if not target_exists:
            raise ValueError(f"Entry {to_entry} not found")
    cur = conn.execute(  # type: ignore[union-attr]
        "INSERT INTO journal_links (from_entry, to_entry, to_entity, link_type) "
        "VALUES (?, ?, ?, ?)",
        (from_entry, to_entry, to_entity, link_type),
    )
    link_id = cur.lastrowid
    assert link_id is not None
    return int(link_id)


def _row_to_item(
    row: dict[str, Any], links: list[dict[str, Any]]
) -> ReflectiveEntryItem:
    return ReflectiveEntryItem(
        id=row["id"],
        agent=row["agent"],
        register=row["register"],
        entry=row["entry"],
        kind=row["kind"],
        session_id=row.get("session_id"),
        revises=row.get("revises"),
        consolidation_data=json_decode(row.get("consolidation_data")),
        links=[JournalLinkItem(**lnk) for lnk in links],
        created_at=row["created_at"],
    )


def _fetch_links(conn: object, entry_ids: list[int]) -> dict[int, list[dict[str, Any]]]:
    """Batch-fetch links for a set of entry IDs."""
    if not entry_ids:
        return {}
    placeholders = ",".join("?" for _ in entry_ids)
    rows = query(
        conn,  # type: ignore[arg-type]
        f"SELECT * FROM journal_links WHERE from_entry IN ({placeholders})",
        tuple(entry_ids),
    )
    result: dict[int, list[dict[str, Any]]] = {eid: [] for eid in entry_ids}
    for r in rows:
        result.setdefault(r["from_entry"], []).append(r)
    return result


def _suggest_links(
    conn: object, entry_id: int, agent: str, entry_text: str
) -> list[dict[str, Any]]:
    """Phase 1.5 assisted linking: find recent entries by the same agent
    that might be related. Simple recency + keyword overlap heuristic.
    """
    rows = query(
        conn,  # type: ignore[arg-type]
        "SELECT id, entry, kind FROM reflective_journal "
        "WHERE agent = ? AND id != ? ORDER BY id DESC LIMIT 10",
        (agent, entry_id),
    )
    suggestions: list[dict[str, Any]] = []
    entry_words = set(entry_text.lower().split())
    for r in rows:
        other_words = set(r["entry"].lower().split())
        overlap = len(entry_words & other_words)
        if overlap >= 3:
            suggestions.append(
                {
                    "to_entry": r["id"],
                    "link_type": "related",
                    "reason": f"{overlap} shared terms",
                    "snippet": r["entry"][:120],
                }
            )
    return suggestions[:5]


@router.get("", response_model=ReflectiveEntryList)
def list_entries(
    agent: str | None = None,
    kind: str | None = None,
    limit: int = Query(20, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> ReflectiveEntryList:
    """List reflective journal entries, newest first."""
    clauses: list[str] = []
    params: list[str | int] = []
    if agent:
        clauses.append("agent = ?")
        params.append(agent)
    if kind:
        if kind not in _VALID_KINDS:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid kind {kind!r}. Must be one of: {sorted(_VALID_KINDS)}",
            )
        clauses.append("kind = ?")
        params.append(kind)

    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""

    conn = cortex_conn()
    try:
        count_row = query(
            conn,
            f"SELECT COUNT(*) as cnt FROM reflective_journal{where}",
            tuple(params),
        )
        total = count_row[0]["cnt"] if count_row else 0

        params.extend([limit, offset])
        rows = query(
            conn,
            f"SELECT * FROM reflective_journal{where} "
            "ORDER BY id DESC LIMIT ? OFFSET ?",
            tuple(params),
        )

        entry_ids = [r["id"] for r in rows]
        links_by_entry = _fetch_links(conn, entry_ids)
    finally:
        conn.close()

    items = [_row_to_item(r, links_by_entry.get(r["id"], [])) for r in rows]
    return ReflectiveEntryList(items=items, total=total)


@router.get("/{entry_id}", response_model=ReflectiveEntryItem)
def get_entry(entry_id: int) -> ReflectiveEntryItem:
    """Get a single reflective journal entry with its links."""
    conn = cortex_conn()
    try:
        rows = query(conn, "SELECT * FROM reflective_journal WHERE id = ?", (entry_id,))
        if not rows:
            raise HTTPException(status_code=404, detail=f"Entry {entry_id} not found")
        links_by_entry = _fetch_links(conn, [entry_id])
    finally:
        conn.close()

    return _row_to_item(rows[0], links_by_entry.get(entry_id, []))


@router.post(
    "", response_model=ReflectiveEntryItem, status_code=status.HTTP_201_CREATED
)
def create_entry(body: ReflectiveEntryCreate) -> ReflectiveEntryItem:
    """Write a reflective journal entry.

    Returns the created entry with links and Phase 1.5 suggested_links.
    """
    if body.kind == "consolidation" and body.consolidation_data is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="consolidation_data is required for kind='consolidation'",
        )
    if body.kind != "consolidation" and body.consolidation_data is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="consolidation_data is only valid for kind='consolidation'",
        )
    if body.kind == "revision" and body.revises is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="revises is required for kind='revision'",
        )

    consolidation_json = None
    if body.consolidation_data is not None:
        consolidation_json = json.dumps(body.consolidation_data.model_dump())

    conn = cortex_conn()
    try:
        if body.revises is not None:
            exists = query(
                conn,
                "SELECT 1 FROM reflective_journal WHERE id = ?",
                (body.revises,),
            )
            if not exists:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"revises target {body.revises} not found",
                )

        cur = conn.execute(
            "INSERT INTO reflective_journal "
            "(agent, register, entry, kind, session_id, revises, consolidation_data) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                body.agent,
                body.register,
                body.entry,
                body.kind,
                body.session_id,
                body.revises,
                consolidation_json,
            ),
        )
        entry_id = cur.lastrowid
        assert entry_id is not None

        if body.links:
            for lnk in body.links:
                if lnk.to_entry is None and lnk.to_entity is None:
                    continue
                conn.execute(
                    "INSERT INTO journal_links (from_entry, to_entry, to_entity, link_type) "
                    "VALUES (?, ?, ?, ?)",
                    (entry_id, lnk.to_entry, lnk.to_entity, lnk.link_type),
                )

        conn.commit()

        rows = query(conn, "SELECT * FROM reflective_journal WHERE id = ?", (entry_id,))
        links_by_entry = _fetch_links(conn, [entry_id])
        suggestions = _suggest_links(conn, entry_id, body.agent, body.entry)
    finally:
        conn.close()

    if not rows:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Entry created but could not be read back",
        )

    item = _row_to_item(rows[0], links_by_entry.get(entry_id, []))
    item.suggested_links = suggestions if suggestions else None
    return item


@router.post("/{entry_id}/links", status_code=status.HTTP_201_CREATED)
def add_link(entry_id: int, body: JournalLinkCreate) -> JournalLinkItem:
    """Add a link from an existing entry to another entry or entity."""
    to_entry = body.to_entry
    to_entity = body.to_entity
    link_type = body.link_type
    if to_entry is None and to_entity is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Either to_entry or to_entity is required",
        )
    if link_type not in _VALID_LINK_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid link_type {link_type!r}. Must be one of: {sorted(_VALID_LINK_TYPES)}",
        )

    conn = cortex_conn()
    try:
        exists = query(
            conn, "SELECT 1 FROM reflective_journal WHERE id = ?", (entry_id,)
        )
        if not exists:
            raise HTTPException(status_code=404, detail=f"Entry {entry_id} not found")

        cur = conn.execute(
            "INSERT INTO journal_links (from_entry, to_entry, to_entity, link_type) "
            "VALUES (?, ?, ?, ?)",
            (entry_id, to_entry, to_entity, link_type),
        )
        conn.commit()
        rows = query(conn, "SELECT * FROM journal_links WHERE id = ?", (cur.lastrowid,))
    finally:
        conn.close()

    if not rows:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Link created but could not be read back",
        )
    return JournalLinkItem(**rows[0])


def _list_entries_impl(**kwargs: object) -> dict[str, object]:
    return list_entries(**kwargs).model_dump(mode="json")


def _get_entry_impl(entry_id: int) -> dict[str, object]:
    return get_entry(entry_id).model_dump(mode="json")


def _create_entry_impl(payload: dict[str, object]) -> dict[str, object]:
    data = create_entry(ReflectiveEntryCreate.model_validate(payload))
    return data.model_dump(mode="json")


def _add_link_impl(entry_id: int, payload: dict[str, object]) -> dict[str, object]:
    data = add_link(entry_id, JournalLinkCreate.model_validate(payload))
    return data.model_dump(mode="json")
