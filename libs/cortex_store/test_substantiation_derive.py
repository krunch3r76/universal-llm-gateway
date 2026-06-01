"""Fork D (G1, thread 1173) — derived substantiation state, deterministic v1."""

from __future__ import annotations

import sqlite3

from cortex_store.dispatch_ops._detectors.substantiation import (
    CONFIRMED,
    SUPPORTED,
    UNSUBSTANTIATED,
    derive_substantiation_state,
)


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(
        """
        CREATE TABLE assertions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entity_id TEXT NOT NULL,
            confidence TEXT,
            superseded_by INTEGER
        );
        """
    )
    return c


def _add(c: sqlite3.Connection, eid: str, confidence: str, superseded_by=None) -> None:
    c.execute(
        "INSERT INTO assertions (entity_id, confidence, superseded_by) VALUES (?, ?, ?)",
        (eid, confidence, superseded_by),
    )
    c.commit()


def test_no_assertions_unsubstantiated() -> None:
    c = _conn()
    assert derive_substantiation_state(c, "x:none") == UNSUBSTANTIATED


def test_confirmed_assertion_confirms() -> None:
    c = _conn()
    _add(c, "x:c", "confirmed")
    assert derive_substantiation_state(c, "x:c") == CONFIRMED


def test_only_believed_is_supported() -> None:
    c = _conn()
    _add(c, "x:b", "believed")
    assert derive_substantiation_state(c, "x:b") == SUPPORTED


def test_superseded_confirmed_does_not_confirm() -> None:
    c = _conn()
    _add(c, "x:s", "confirmed", superseded_by=99)
    _add(c, "x:s", "believed")
    assert derive_substantiation_state(c, "x:s") == SUPPORTED
