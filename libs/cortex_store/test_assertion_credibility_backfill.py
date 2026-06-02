"""Tests for external http(s) assertion credibility backfill (option b)."""

from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path

import pytest

from cortex_store.assertion_credibility_backfill import (
    external_http_source_keys,
    planned_credibility_band,
    run_external_credibility_backfill,
)

_MIG_PATH = (
    Path(__file__).parent / "migrations" / "050_status_trait_normalization_phase0.py"
)
_spec = importlib.util.spec_from_file_location(
    "migration_050_status_trait_normalization_phase0", _MIG_PATH
)
assert _spec is not None and _spec.loader is not None
migration_050 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(migration_050)


@pytest.fixture()
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(
        """
        CREATE TABLE entities (id TEXT PRIMARY KEY, type TEXT NOT NULL);
        CREATE TABLE assertions (
            id INTEGER PRIMARY KEY,
            entity_id TEXT,
            claim TEXT,
            confidence TEXT,
            evidence_uris TEXT,
            seeded_by TEXT,
            derivation_type TEXT,
            review_status TEXT,
            superseded_by INTEGER
        );
        """
    )
    migration_050.migrate(c)
    return c


def _insert(
    conn: sqlite3.Connection,
    *,
    evidence_uris: str | None,
    credibility: str | None = None,
    review_status: str | None = None,
) -> int:
    cur = conn.execute(
        "INSERT INTO assertions (entity_id, claim, confidence, credibility, "
        "evidence_uris, review_status) VALUES (?, ?, ?, ?, ?, ?)",
        ("person:x", "claim", "confirmed", credibility, evidence_uris, review_status),
    )
    return int(cur.lastrowid)


def test_external_http_keys_extracts_hosts() -> None:
    keys = external_http_source_keys(
        '["https://www.FINRA.org/foo", "cortex:notes/bar"]'
    )
    assert keys == ("finra.org",)


def test_planned_gov_host_authority() -> None:
    band = planned_credibility_band(
        credibility=None,
        evidence_uris='["https://boe.ca.gov/form"]',
        review_status=None,
    )
    assert band == "authority"


def test_planned_manual_list_external_kb() -> None:
    band = planned_credibility_band(
        credibility=None,
        evidence_uris='["https://docs.anthropic.com/en/api"]',
        review_status=None,
    )
    assert band == "external-KB"


def test_planned_unlisted_http_stays_none() -> None:
    band = planned_credibility_band(
        credibility=None,
        evidence_uris='["https://futuresearch.ai/blog/x"]',
        review_status=None,
    )
    assert band is None


def test_planned_stored_credibility_not_overwritten() -> None:
    band = planned_credibility_band(
        credibility="unrated",
        evidence_uris='["https://boe.ca.gov/form"]',
        review_status=None,
    )
    assert band is None


def test_run_backfill_apply_and_idempotent(conn: sqlite3.Connection) -> None:
    _insert(
        conn,
        evidence_uris='["https://leginfo.legislature.ca.gov/statute"]',
    )
    _insert(
        conn,
        evidence_uris='["https://coindesk.com/article"]',
    )
    first = run_external_credibility_backfill(conn, dry_run=False)
    assert first.assertions_updated == 1
    assert first.by_band == {"authority": 1}
    row = conn.execute(
        "SELECT credibility FROM assertions WHERE evidence_uris LIKE '%leginfo%'"
    ).fetchone()
    assert row["credibility"] == "authority"
    second = run_external_credibility_backfill(conn, dry_run=False)
    assert second.assertions_updated == 0
