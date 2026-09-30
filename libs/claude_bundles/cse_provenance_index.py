"""Keyed checkpoint-plus-tail index of ``cse.provenance.episode`` rows in ``registry.jsonl``.

``registry.jsonl`` is append-only and never compacted; on Jupiter 352k of its
471k lines (231 MB of 292 MB) are ``cse.provenance.episode`` rows (a:36941).
Every request-path reader of those rows — paste resolve
(``cse_provenance_resolve.resolve``), harvest identity
(``cdp_ask.cse_session_harvest_identity``), hub enrichment
(``cse_provenance_enrich``) and ``append_episode`` itself — needs the episodes
for **one** ``chat_url``, ``registration_id`` or ``correlation_id``, not the
whole history. This module keeps a SQLite file, ``provenance_episode.sqlite``,
beside the journal holding exactly the episode rows (verbatim JSON text, in
journal order) with an index per lookup key, plus the byte offset into
``registry.jsonl`` up to which rows have been copied. A refresh reads only the
journal tail past that cursor; a lookup then runs one indexed query and parses
only the rows it returns.

Why SQLite rather than one projection file per key: the three lookup keys
would need three projections, and the ``attachment_fold.jsonl`` pattern
(``cdp_registry.journal_projection``) still parses every projected row per
read — for episodes that would be 75% of the journal again. SQLite gives all
three keys, journal-order results, and a cursor that commits atomically with
the rows it covers. The attachment fold projection is untouched.

Invariants:

* ``sqlite3`` locking is the single-writer guarantee for the fold: the tail is
  copied under ``BEGIN IMMEDIATE`` and the cursor is re-read inside that
  transaction, so two processes refreshing at once never double-copy a row.
  ``append_log`` stays lock-free, which is why a partial trailing journal line
  is left for the next pass.
* ``seq`` (rowid) order equals journal order, so a keyed result replayed by
  callers (supersede-chain walks, latest-per-registration) matches the result
  the full-history reader produced.
* Cursor past EOF, a missing or unreadable index file, or a schema version
  mismatch means the journal was replaced or the index is stale: the index is
  rebuilt from offset 0 inside one transaction.
* Only ``cse.provenance.episode`` rows are stored; every other journal event is
  skipped at fold time, so this is a per-event index, not an index of
  ``registry.jsonl`` as a whole (that remains an a:36920 candidate).
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from claude_bundles import cdp_registry_store as _store

INDEX_NAME = "provenance_episode.sqlite"
EPISODE_EVENT = "cse.provenance.episode"
SCHEMA_VERSION = 1
LOOKUP_KEYS: frozenset[str] = frozenset(
    {"chat_url", "registration_id", "correlation_id"}
)

# ``append_log`` serializes with sort_keys=True and default separators, so this
# literal is present in every episode line; it is a prefilter only — the parsed
# ``event`` field is what decides membership.
_EPISODE_MARKER = b'"event": "cse.provenance.episode"'
_LOCK_TIMEOUT_S = 30.0

_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS episode ("
    " seq INTEGER PRIMARY KEY,"
    " chat_url TEXT,"
    " registration_id TEXT,"
    " correlation_id TEXT,"
    " row TEXT NOT NULL)",
    "CREATE INDEX IF NOT EXISTS episode_chat_url ON episode(chat_url, seq)",
    "CREATE INDEX IF NOT EXISTS episode_registration_id ON episode(registration_id, seq)",
    "CREATE INDEX IF NOT EXISTS episode_correlation_id ON episode(correlation_id, seq)",
    "CREATE TABLE IF NOT EXISTS journal_cursor ("
    " id INTEGER PRIMARY KEY CHECK (id = 1),"
    " offset INTEGER NOT NULL)",
)


@dataclass(frozen=True)
class IndexPass:
    """Receipt for one ``refresh`` call — how much of the journal was read.

    ``bytes_consumed`` / ``rows_consumed`` cover only the journal tail past the
    cursor; ``rows_indexed`` counts the episode rows copied into the index.
    ``rebuilt`` marks a from-zero pass (first run, truncated journal, missing or
    corrupt index, schema change). A steady-state lookup shows
    ``bytes_consumed == 0``.
    """

    bytes_consumed: int
    rows_consumed: int
    rows_indexed: int
    rebuilt: bool


def index_path() -> Path:
    """Index file beside the live ``REGISTRY_LOG`` (tests repoint the log per case)."""
    return _store.REGISTRY_LOG.with_name(INDEX_NAME)


def _remove_index_files(path: Path) -> None:
    for suffix in ("", "-wal", "-shm"):
        with_suffix = Path(f"{path}{suffix}")
        if with_suffix.exists():
            with_suffix.unlink()


def _open(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=_LOCK_TIMEOUT_S, isolation_level=None)
    conn.execute("PRAGMA journal_mode=WAL")
    if conn.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION:
        conn.execute("BEGIN IMMEDIATE")
        if conn.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION:
            conn.execute("DROP TABLE IF EXISTS episode")
            conn.execute("DROP TABLE IF EXISTS journal_cursor")
            for statement in _SCHEMA:
                conn.execute(statement)
            conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        conn.execute("COMMIT")
    return conn


def _connect() -> sqlite3.Connection:
    """Open the index, recreating it once when the file is not a usable database.

    ``sqlite3.OperationalError`` (locked, busy) propagates; only non-operational
    ``DatabaseError`` (not a database, malformed) triggers the rebuild, because
    the index is derived state and the journal is the source of truth.
    """
    path = index_path()
    try:
        return _open(path)
    except sqlite3.DatabaseError as exc:
        if isinstance(exc, sqlite3.OperationalError):
            raise
        _remove_index_files(path)
        return _open(path)


def _cursor_offset(conn: sqlite3.Connection) -> int | None:
    """Persisted journal byte offset, or ``None`` before the first fold."""
    row = conn.execute("SELECT offset FROM journal_cursor WHERE id = 1").fetchone()
    return int(row[0]) if row is not None else None


def _journal_tail(offset: int) -> Iterator[bytes]:
    """Yield complete journal lines from *offset*; a partial trailing line is left."""
    with _store.REGISTRY_LOG.open("rb") as fh:
        fh.seek(offset)
        for raw in fh:
            if not raw.endswith(b"\n"):
                return
            yield raw


def _fold_tail(conn: sqlite3.Connection, offset: int) -> tuple[int, int, int]:
    consumed = rows = indexed = 0
    for raw in _journal_tail(offset):
        consumed += len(raw)
        if not raw.strip():
            continue
        rows += 1
        if _EPISODE_MARKER not in raw:
            continue
        record = json.loads(raw)
        if not isinstance(record, dict) or record.get("event") != EPISODE_EVENT:
            continue
        conn.execute(
            "INSERT INTO episode (chat_url, registration_id, correlation_id, row)"
            " VALUES (?, ?, ?, ?)",
            (
                record.get("chat_url"),
                record.get("registration_id"),
                record.get("correlation_id"),
                raw.decode("utf-8").rstrip("\n"),
            ),
        )
        indexed += 1
    return consumed, rows, indexed


def _refresh(conn: sqlite3.Connection) -> IndexPass:
    log = _store.REGISTRY_LOG
    size = log.stat().st_size if log.exists() else 0
    # Steady state: nothing appended since the last pass — no write transaction.
    if _cursor_offset(conn) == size:
        return IndexPass(0, 0, 0, False)
    conn.execute("BEGIN IMMEDIATE")
    try:
        cursor = _cursor_offset(conn)
        rebuilt = cursor is None or cursor > size
        offset = 0 if rebuilt else cursor
        if rebuilt:
            conn.execute("DELETE FROM episode")
        consumed, rows, indexed = (
            _fold_tail(conn, offset) if size > offset else (0, 0, 0)
        )
        conn.execute(
            "INSERT INTO journal_cursor (id, offset) VALUES (1, ?)"
            " ON CONFLICT(id) DO UPDATE SET offset = excluded.offset",
            (offset + consumed,),
        )
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return IndexPass(consumed, rows, indexed, rebuilt)


def refresh() -> IndexPass:
    """Copy episode rows appended to the journal since the last pass into the index.

    Reads ``REGISTRY_LOG`` from the persisted byte cursor to the last newline
    and inserts only ``cse.provenance.episode`` rows, then advances the cursor
    in the same transaction. A cursor beyond the journal size (journal replaced
    or truncated) empties the index and refolds from offset 0. Raises
    ``json.JSONDecodeError`` on a corrupt tail line, leaving the cursor before
    it. Returns the pass receipt so callers and tests can assert what was read.
    """
    with closing(_connect()) as conn:
        return _refresh(conn)


def _decode(rows: list[tuple[str]]) -> list[dict[str, Any]]:
    return [json.loads(row[0]) for row in rows]


def _require_key(key: str) -> None:
    if key not in LOOKUP_KEYS:
        raise ValueError(f"unsupported episode lookup key {key!r}")


def episode_rows(key: str, value: str) -> list[dict[str, Any]]:
    """Return the raw episode records whose *key* column equals *value*, in journal order.

    *key* is one of ``LOOKUP_KEYS``. Refreshes the index first so rows appended
    since the previous call are included; only the matching rows are parsed.
    """
    _require_key(key)
    with closing(_connect()) as conn:
        _refresh(conn)
        rows = conn.execute(
            f"SELECT row FROM episode WHERE {key} = ? ORDER BY seq", (value,)
        ).fetchall()
    return _decode(rows)


def latest_episode_row(key: str, value: str) -> dict[str, Any] | None:
    """Return the newest raw episode record whose *key* column equals *value*, or ``None``.

    Same refresh-then-query shape as ``episode_rows`` but parses one row at most.
    """
    _require_key(key)
    with closing(_connect()) as conn:
        _refresh(conn)
        row = conn.execute(
            f"SELECT row FROM episode WHERE {key} = ? ORDER BY seq DESC LIMIT 1",
            (value,),
        ).fetchone()
    return json.loads(row[0]) if row is not None else None


def all_episode_rows() -> list[dict[str, Any]]:
    """Return every indexed episode record in journal order — the whole history.

    History-wide readers only (tests, audits). Request paths use the keyed
    lookups; this parses every episode row and costs what the old full-file
    reader cost minus the non-episode 25% of the journal.
    """
    with closing(_connect()) as conn:
        _refresh(conn)
        rows = conn.execute("SELECT row FROM episode ORDER BY seq").fetchall()
    return _decode(rows)


def remove_index() -> None:
    """Delete the index files so the next read rebuilds from the journal (tests, ops)."""
    _remove_index_files(index_path())


__all__ = [
    "EPISODE_EVENT",
    "INDEX_NAME",
    "IndexPass",
    "LOOKUP_KEYS",
    "all_episode_rows",
    "episode_rows",
    "index_path",
    "latest_episode_row",
    "refresh",
    "remove_index",
]
