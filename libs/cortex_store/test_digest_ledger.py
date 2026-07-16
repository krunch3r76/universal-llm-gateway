"""Hermetic tests for digest_ledger migration + CRUD + pure helpers."""

from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path

import pytest

from cortex_store.digest_ledger import (
    compute_entry_content_sha256,
    derive_valid_from_hint,
    lookup,
    lookup_latest_for_anchor,
    map_p_class_to_derivation_confidence,
    write,
)

_MIG_PATH = Path(__file__).parent / "migrations" / "068_digest_ledger.py"
_spec = importlib.util.spec_from_file_location("migration_068_digest_ledger", _MIG_PATH)
assert _spec is not None and _spec.loader is not None
migration_068 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(migration_068)


@pytest.fixture()
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    migration_068.migrate(c)
    return c


@pytest.mark.offline
def test_compute_entry_content_sha256_format() -> None:
    digest = compute_entry_content_sha256("hello journal")
    assert digest.startswith("sha256:")
    assert len(digest) == len("sha256:") + 64


@pytest.mark.offline
@pytest.mark.parametrize(
    ("p_class", "expected"),
    [
        ("P1", ("user_statement", "confirmed")),
        ("P2", ("user_statement", "confirmed")),
        ("P2²", ("user_statement", "confirmed")),
        ("P3", ("inference", "suspected")),
    ],
)
def test_map_p_class_to_derivation_confidence(
    p_class: str, expected: tuple[str, str]
) -> None:
    assert map_p_class_to_derivation_confidence(p_class) == expected


@pytest.mark.offline
def test_derive_valid_from_hint_present_and_absent() -> None:
    assert derive_valid_from_hint({"valid_from": "2026-07-13"}) == "2026-07-13"
    assert (
        derive_valid_from_hint({"valid_from_hint": "2026-07-13T10:11:00Z"})
        == "2026-07-13"
    )
    assert derive_valid_from_hint({"claim": "Payment on 2026-07-13"}) == "2026-07-13"
    assert derive_valid_from_hint({}) is None


@pytest.mark.offline
def test_derive_valid_from_hint_month_name_and_explicit_wins() -> None:
    assert (
        derive_valid_from_hint(
            {
                "valid_from": "2026-08-01",
                "claim": "Carol stage deadline August 16, 2026",
            }
        )
        == "2026-08-01"
    )
    assert (
        derive_valid_from_hint({"claim": "PG&E must pay by July 17, 2026"})
        == "2026-07-17"
    )
    assert (
        derive_valid_from_hint({"claim": "Appointment on July 22, 2026 at noon"})
        == "2026-07-22"
    )
    assert derive_valid_from_hint({"claim": "No calendar reference here"}) is None


@pytest.mark.offline
def test_write_lookup_hit_and_miss(conn: sqlite3.Connection) -> None:
    sha = compute_entry_content_sha256("entry body")
    row_id = write(
        conn,
        journal_entity_id="document:journal",
        entry_anchor="2026-07-13#health",
        content_sha256=sha,
        emitted_ids=["assertion:1", "assertion:2"],
        staging_batch_id="staging:batch-1",
        verify_verdicts={"claim-1": "pass"},
    )
    assert row_id == 1

    hit = lookup(conn, "document:journal", "2026-07-13#health", sha)
    assert hit is not None
    assert hit["id"] == row_id
    assert hit["emitted_ids"] == ["assertion:1", "assertion:2"]
    assert hit["staging_batch_id"] == "staging:batch-1"
    assert hit["verify_verdicts"] == {"claim-1": "pass"}

    miss = lookup(conn, "document:journal", "2026-07-13#health", "sha256:deadbeef")
    assert miss is None


@pytest.mark.offline
def test_lookup_latest_for_anchor(conn: sqlite3.Connection) -> None:
    sha_old = compute_entry_content_sha256("v1")
    sha_new = compute_entry_content_sha256("v2")
    write(
        conn,
        journal_entity_id="document:journal",
        entry_anchor="2026-07-13#wells-fargo",
        content_sha256=sha_old,
        emitted_ids=[1],
    )
    write(
        conn,
        journal_entity_id="document:journal",
        entry_anchor="2026-07-13#wells-fargo",
        content_sha256=sha_new,
        emitted_ids=[2],
    )

    latest = lookup_latest_for_anchor(conn, "document:journal", "2026-07-13#wells-fargo")
    assert latest is not None
    assert latest["content_sha256"] == sha_new
    assert latest["emitted_ids"] == [2]


@pytest.mark.offline
def test_unique_constraint_violation(conn: sqlite3.Connection) -> None:
    sha = compute_entry_content_sha256("same body")
    write(
        conn,
        journal_entity_id="document:journal",
        entry_anchor="2026-07-13#pge",
        content_sha256=sha,
        emitted_ids=[],
    )
    with pytest.raises(sqlite3.IntegrityError):
        write(
            conn,
            journal_entity_id="document:journal",
            entry_anchor="2026-07-13#pge",
            content_sha256=sha,
            emitted_ids=["duplicate"],
        )


@pytest.mark.offline
def test_migration_idempotent_rerun(conn: sqlite3.Connection) -> None:
    migration_068.migrate(conn)
    count = conn.execute("SELECT COUNT(*) AS n FROM digest_ledger").fetchone()["n"]
    assert count >= 0
