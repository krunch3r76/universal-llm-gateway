"""Checkpoint-plus-tail projection of ``registry.jsonl`` for the attachment fold.

``registry.jsonl`` is append-only and never compacted (292 MB / 471k lines on
Jupiter, a:36920); 75% of it is ``cse.provenance.episode`` and hygiene rows the
attachment fold never applies. Every fold-path reader — ``fold_attachment_journal``,
``has_attachment_observed``, ``has_standdown_token`` — matches only the six
``FOLD_EVENTS``, so this module keeps a second file, ``attachment_fold.jsonl``,
holding exactly those rows in journal order plus a byte cursor into
``registry.jsonl``. A pass reads the journal from the cursor to the last complete
line, copies matching rows to the projection, and advances the cursor; readers
then parse the projection instead of the whole history.

Invariants:

* ``project_fold_tail`` runs under ``ports_lock`` (caller-held). That lock is the
  single-writer guarantee for projection and cursor; ``append_log`` itself stays
  lock-free, which is why a partial trailing line is left for the next pass.
* Projection order equals journal order for the projected rows, so replay over
  the projection yields the same fold as replay over the full journal.
* Cursor past EOF or a missing projection means the journal was replaced or the
  index was deleted: the projection is rebuilt from offset 0.
* A crash between projection append and cursor write re-copies the same rows on
  the next pass; every fold handler is idempotent per row, so duplicates do not
  change fold results.

Seat-axis readers (``fold_seat_journal``, ``verify_seat_fold_invariant``) apply
``seat_lane_bound`` across all ids and stay on ``cdp_registry_store.read_registry_log``.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from claude_bundles import cdp_registry_store as _store

PROJECTION_NAME = "attachment_fold.jsonl"
CURSOR_NAME = "attachment_fold.cursor.json"

FOLD_EVENTS: frozenset[str] = frozenset(
    {
        "attachment_observed",
        "session_address_bound",
        "attachment_bound",
        "standdown_pasted",
        "standdown_unreachable",
        "detached",
    }
)


def projection_path() -> Path:
    """Projection file beside the live ``REGISTRY_LOG`` (tests repoint the log per case)."""
    return _store.REGISTRY_LOG.with_name(PROJECTION_NAME)


def _cursor_path() -> Path:
    return _store.REGISTRY_LOG.with_name(CURSOR_NAME)


def _read_cursor() -> int:
    try:
        payload = json.loads(_cursor_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 0
    offset = payload.get("offset") if isinstance(payload, dict) else None
    return int(offset) if isinstance(offset, int) and offset >= 0 else 0


def _write_cursor(offset: int) -> None:
    path = _cursor_path()
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"offset": offset}), encoding="utf-8")
    os.replace(tmp, path)


@dataclass(frozen=True)
class ProjectionPass:
    """Receipt for one ``project_fold_tail`` call — how much of the journal was read.

    ``bytes_consumed`` / ``rows_consumed`` cover only the tail past the cursor;
    ``rows_projected`` counts those copied into the projection. ``rebuilt`` marks
    a from-zero pass (first run, truncated journal, or deleted projection).
    """

    bytes_consumed: int
    rows_consumed: int
    rows_projected: int
    rebuilt: bool


def project_fold_tail() -> ProjectionPass:
    """Copy fold-relevant journal lines appended since the last pass into the projection.

    Caller holds ``ports_lock``. Reads ``REGISTRY_LOG`` from the persisted byte
    cursor to the last newline, parses only those rows, appends the ones whose
    ``event`` is in ``FOLD_EVENTS`` to ``attachment_fold.jsonl``, then advances the
    cursor. A cursor beyond the current journal size, or a missing projection,
    triggers a rebuild from offset 0. Raises ``json.JSONDecodeError`` on a corrupt
    tail line, leaving the cursor before it (same failure the full reader had).
    Returns the pass receipt so callers and tests can assert what was read.
    """
    log = _store.REGISTRY_LOG
    projection = projection_path()
    size = log.stat().st_size if log.exists() else 0
    offset = _read_cursor()
    rebuilt = False
    if offset > size or not projection.exists():
        projection.parent.mkdir(parents=True, exist_ok=True)
        projection.write_bytes(b"")
        offset = 0
        rebuilt = True
    consumed = 0
    rows = 0
    projected: list[bytes] = []
    if size > offset:
        with log.open("rb") as fh:
            fh.seek(offset)
            for raw in fh:
                if not raw.endswith(b"\n"):
                    break
                consumed += len(raw)
                if not raw.strip():
                    continue
                rows += 1
                row = json.loads(raw)
                if isinstance(row, dict) and row.get("event") in FOLD_EVENTS:
                    projected.append(raw)
    if projected:
        with projection.open("ab") as fh:
            fh.writelines(projected)
            fh.flush()
            os.fsync(fh.fileno())
    if consumed or rebuilt:
        _write_cursor(offset + consumed)
    return ProjectionPass(
        bytes_consumed=consumed,
        rows_consumed=rows,
        rows_projected=len(projected),
        rebuilt=rebuilt,
    )


def read_fold_rows() -> list[dict[str, Any]]:
    """Return the projected fold rows in journal order after folding in the new tail.

    Caller holds ``ports_lock``. Runs ``project_fold_tail`` first so the result
    includes lines appended since the previous pass, then parses only
    ``attachment_fold.jsonl`` — never the full ``registry.jsonl``.
    """
    project_fold_tail()
    out: list[dict[str, Any]] = []
    with projection_path().open("rb") as fh:
        for raw in fh:
            if not raw.strip():
                continue
            row = json.loads(raw)
            if isinstance(row, dict):
                out.append(row)
    return out
