"""Timer-driven entities_fts reconciler for cortex-api (R4).

Repairs index drift that sync-on-write missed: missing FTS rows for live
entities, stale FTS rows for non-live ids, and alias/render mismatches on a
slow full-compare cadence. Never gates on ``entities_fts_stats.stale`` — row
count parity can hide per-id gaps.
"""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import time
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from universal_event_bus.events import Event
from universal_event_bus.events.factory import event_factory
from universal_logging import get_logger

from .db import WRITE_LOCK, cortex_conn
from .entity_aliases import live_alias_rows
from .entity_fts import (
    _aliases_index_text,
    _slug_index_text,
    _src_fname_index_text,
    entities_fts_table_exists,
    reindex_entity_fts_conn,
)
from .event_publisher import record

logger = get_logger("cortex-api.entity_fts_reconcile")

_ID_DIFF_INTERVAL_S: int = int(os.getenv("CORTEX_FTS_ID_DIFF_INTERVAL", "60"))
_FULL_COMPARE_INTERVAL_S: int = int(
    os.getenv("CORTEX_FTS_FULL_COMPARE_INTERVAL", "900")
)

_LIVE_ENTITY_WHERE = (
    "lifecycle IS NULL OR lifecycle NOT IN ('merged', 'deprecated', 'reaped')"
)

_DELETE_STALE_FTS_SQL = (
    "DELETE FROM entities_fts WHERE entity_id IN ("
    "SELECT ef.entity_id FROM entities_fts ef "
    "LEFT JOIN entities e ON e.id = ef.entity_id "
    "WHERE e.id IS NULL "
    "OR e.lifecycle IN ('merged', 'deprecated', 'reaped')"
    ")"
)


@dataclass(frozen=True)
class ReconcilePassResult:
    """Counts from one reconcile pass (observability + tests)."""

    pass_kind: str
    missing: int
    mismatched: int
    repaired: int
    duration_ms: float


_pass_running = False
_last_full_compare_mono: float = 0.0


def id_diff_sets(
    conn: sqlite3.Connection,
) -> tuple[set[str], set[str], set[str], set[str]]:
    """Return live ids, FTS ids, live-without-FTS, and FTS ids not live."""
    if not entities_fts_table_exists(conn):
        live_rows = conn.execute(
            f"SELECT id FROM entities WHERE {_LIVE_ENTITY_WHERE}"
        ).fetchall()
        live_ids = {str(r[0]) for r in live_rows}
        return live_ids, set(), live_ids, set()

    live_rows = conn.execute(
        f"SELECT id FROM entities WHERE {_LIVE_ENTITY_WHERE}"
    ).fetchall()
    fts_rows = conn.execute("SELECT entity_id FROM entities_fts").fetchall()
    live_ids = {str(r[0]) for r in live_rows}
    fts_ids = {str(r[0]) for r in fts_rows}
    missing_live = live_ids - fts_ids
    stale_fts = fts_ids - live_ids
    return live_ids, fts_ids, missing_live, stale_fts


def _aliases_index_text_with_map(
    entity_id: str,
    name: str | None,
    row_aliases: object,
    live_aliases_by_entity: dict[str, list[str]],
) -> str:
    """Same rendered alias text as ``_aliases_index_text`` without re-scanning aliases."""
    parts: list[str] = []
    seen: set[str] = set()

    def add(text: str | None) -> None:
        if not text:
            return
        t = str(text).strip()
        if not t or t in seen:
            return
        seen.add(t)
        parts.append(t)

    if name:
        add(name)
        add(" ".join(name.split()))

    if isinstance(row_aliases, list):
        for alias in row_aliases:
            add(str(alias))
    elif isinstance(row_aliases, str) and row_aliases.strip():
        try:
            parsed = json.loads(row_aliases)
            if isinstance(parsed, list):
                for alias in parsed:
                    add(str(alias))
        except (json.JSONDecodeError, TypeError):
            add(row_aliases)

    for alias in live_aliases_by_entity.get(entity_id, ()):
        add(alias)

    return " ".join(parts)


def _build_live_alias_map(conn: sqlite3.Connection) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = defaultdict(list)
    for eid, _etype, alias in live_alias_rows(conn):
        grouped[str(eid)].append(str(alias))
    return dict(grouped)


def _expected_fts_row(
    conn: sqlite3.Connection,
    row: sqlite3.Row | tuple[Any, ...],
    alias_map: dict[str, list[str]],
) -> dict[str, str]:
    eid, _etype, name, description, source_uri, aliases, _lifecycle = row
    eid_s = str(eid)
    return {
        "name": name or "",
        "aliases": _aliases_index_text_with_map(
            eid_s, name, aliases, alias_map
        ),
        "slug": _slug_index_text(eid_s),
        "src_fname": _src_fname_index_text(source_uri),
        "description": description or "",
    }


@event_factory
def entities_fts_reconciled(
    *,
    pass_kind: str,
    missing: int,
    mismatched: int,
    repaired: int,
    duration_ms: int,
) -> Event:
    ev = Event(
        signal="cortex.entities_fts.reconciled",
        role="observation",
        scope="global",
        payload={
            "pass_kind": pass_kind,
            "missing": missing,
            "mismatched": mismatched,
            "repaired": repaired,
            "duration_ms": duration_ms,
        },
    )
    record(ev.signal, **ev.payload)
    return ev


def _emit_pass_result(result: ReconcilePassResult) -> None:
    entities_fts_reconciled(
        pass_kind=result.pass_kind,
        missing=result.missing,
        mismatched=result.mismatched,
        repaired=result.repaired,
        duration_ms=int(round(result.duration_ms)),
    )


def reconcile_id_diff_pass(conn: sqlite3.Connection) -> ReconcilePassResult:
    """Repair live entities missing from FTS and delete non-live FTS rows."""
    started = time.perf_counter()
    if not entities_fts_table_exists(conn):
        return ReconcilePassResult("id_diff", 0, 0, 0, 0.0)

    _live_ids, _fts_ids, missing_live, _stale_fts = id_diff_sets(conn)
    missing = len(missing_live)
    repaired = 0
    for entity_id in sorted(missing_live):
        reindex_entity_fts_conn(conn, entity_id)
        repaired += 1

    delete_cur = conn.execute(_DELETE_STALE_FTS_SQL)
    repaired += int(delete_cur.rowcount or 0)

    conn.commit()
    duration_ms = (time.perf_counter() - started) * 1000.0
    result = ReconcilePassResult(
        pass_kind="id_diff",
        missing=missing,
        mismatched=0,
        repaired=repaired,
        duration_ms=duration_ms,
    )
    _emit_pass_result(result)
    return result


def reconcile_full_compare_pass(conn: sqlite3.Connection) -> ReconcilePassResult:
    """Reindex live entities whose FTS row text differs from the write-path renderer."""
    started = time.perf_counter()
    if not entities_fts_table_exists(conn):
        return ReconcilePassResult("full_compare", 0, 0, 0, 0.0)

    alias_map = _build_live_alias_map(conn)
    live_rows = conn.execute(
        "SELECT id, type, name, description, source_uri, aliases, lifecycle "
        f"FROM entities WHERE {_LIVE_ENTITY_WHERE}"
    ).fetchall()
    fts_by_id = {
        str(r["entity_id"]): r
        for r in conn.execute(
            "SELECT entity_id, name, aliases, slug, src_fname, description "
            "FROM entities_fts"
        ).fetchall()
    }

    missing = 0
    mismatched = 0
    repaired = 0
    compare_fields = ("name", "aliases", "slug", "src_fname", "description")

    for row in live_rows:
        eid = str(row[0])
        fts_row = fts_by_id.get(eid)
        if fts_row is None:
            missing += 1
            reindex_entity_fts_conn(conn, eid)
            repaired += 1
            continue
        expected = _expected_fts_row(conn, row, alias_map)
        if any(expected[f] != (fts_row[f] or "") for f in compare_fields):
            mismatched += 1
            reindex_entity_fts_conn(conn, eid)
            repaired += 1

    conn.commit()
    duration_ms = (time.perf_counter() - started) * 1000.0
    result = ReconcilePassResult(
        pass_kind="full_compare",
        missing=missing,
        mismatched=mismatched,
        repaired=repaired,
        duration_ms=duration_ms,
    )
    _emit_pass_result(result)
    return result


def _run_wake_passes() -> None:
    global _last_full_compare_mono
    with WRITE_LOCK, cortex_conn() as conn:
        reconcile_id_diff_pass(conn)

    now = time.monotonic()
    if _last_full_compare_mono <= 0.0:
        _last_full_compare_mono = now
        return
    if now - _last_full_compare_mono < _FULL_COMPARE_INTERVAL_S:
        return

    with WRITE_LOCK, cortex_conn() as conn:
        reconcile_full_compare_pass(conn)
    _last_full_compare_mono = time.monotonic()


async def run_entity_fts_reconcile() -> None:
    """Background loop: id-set diff every wake; full compare on the slow interval."""
    global _pass_running
    while True:
        await asyncio.sleep(_ID_DIFF_INTERVAL_S)
        if _pass_running:
            continue
        _pass_running = True
        try:
            await asyncio.to_thread(_run_wake_passes)
        except Exception:
            logger.warning("entities_fts reconcile wake failed", exc_info=True)
        finally:
            _pass_running = False


def aliases_index_text_matches_renderer(
    conn: sqlite3.Connection,
    entity_id: str,
    name: str | None,
    row_aliases: object,
) -> bool:
    """Test helper: prefetched alias map matches ``_aliases_index_text`` output."""
    alias_map = _build_live_alias_map(conn)
    rendered = _aliases_index_text_with_map(
        entity_id, name, row_aliases, alias_map
    )
    direct = _aliases_index_text(conn, entity_id, name, row_aliases)
    return rendered == direct
