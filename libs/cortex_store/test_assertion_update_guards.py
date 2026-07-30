"""PATCH /assertions/{id} write guards — arc 6386 operator bind 2026-07-30.

valid_from is fill-only, predicate_form may not silently change class, and the
create-time seeded ledger survives a later predicate writeback.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cortex_store.claim_hash import compute_claim_hash

_ENTITY = "account:chase-mortgage-8787"
_CLAIM = (
    "On 2026-04-13, Nell Cruz opened Chase case ECW260413-02188 and documented "
    "work order #953902037 for a request to extend the escrow shortage spread."
)
_STATE_PREDICATE = "status(account:chase-mortgage-8787, not_approved, current)"


def _seed(
    db_path: Path,
    *,
    assertion_id: int,
    valid_from: str | None,
    predicate_form: str | None,
    raw_predicate_form: str | None = None,
) -> None:
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT OR IGNORE INTO entities (id, type, name) VALUES (?, 'account', ?)",
        (_ENTITY, _ENTITY),
    )
    conn.execute(
        "INSERT INTO assertions "
        "(id, entity_id, claim, confidence, evidence, claim_hash, observed_at, "
        "valid_from, review_status, predicate_form, raw_predicate_form) "
        "VALUES (?, ?, ?, 'confirmed', 'fixture', ?, '2026-07-30T00:00:00Z', "
        "?, 'committed', ?, ?)",
        (
            assertion_id,
            _ENTITY,
            _CLAIM,
            compute_claim_hash(_ENTITY, _CLAIM),
            valid_from,
            predicate_form,
            raw_predicate_form,
        ),
    )
    conn.commit()
    conn.close()


def _stored(db_path: Path, assertion_id: int, column: str):
    conn = sqlite3.connect(db_path)
    row = conn.execute(
        f"SELECT {column} FROM assertions WHERE id = ?", (assertion_id,)
    ).fetchone()
    conn.close()
    return row[0] if row else None


def test_valid_from_fills_an_empty_anchor(
    cortex_client: TestClient, migrated_db_path: Path
) -> None:
    _seed(migrated_db_path, assertion_id=910001, valid_from=None, predicate_form=None)

    resp = cortex_client.patch("/assertions/910001", json={"valid_from": "2026-04-13"})

    assert resp.status_code == 200, resp.text
    assert resp.json()["item"]["valid_from"] == "2026-04-13"
    assert _stored(migrated_db_path, 910001, "valid_from") == "2026-04-13"


def test_valid_from_refuses_to_move_an_existing_anchor(
    cortex_client: TestClient, migrated_db_path: Path
) -> None:
    _seed(
        migrated_db_path,
        assertion_id=910002,
        valid_from="2026-06-26",
        predicate_form=None,
    )

    resp = cortex_client.patch("/assertions/910002", json={"valid_from": "2026-04-13"})

    assert resp.status_code == 409, resp.text
    assert "fill-only" in resp.json()["detail"]
    assert _stored(migrated_db_path, 910002, "valid_from") == "2026-06-26"


def test_valid_from_idempotent_rewrite_is_allowed(
    cortex_client: TestClient, migrated_db_path: Path
) -> None:
    _seed(
        migrated_db_path,
        assertion_id=910003,
        valid_from="2026-04-13",
        predicate_form=None,
    )

    resp = cortex_client.patch("/assertions/910003", json={"valid_from": "2026-04-13"})

    assert resp.status_code == 200, resp.text


def test_force_moves_an_existing_anchor(
    cortex_client: TestClient, migrated_db_path: Path
) -> None:
    _seed(
        migrated_db_path,
        assertion_id=910004,
        valid_from="2026-06-26",
        predicate_form=None,
    )

    resp = cortex_client.patch(
        "/assertions/910004", json={"valid_from": "2026-04-13", "force": True}
    )

    assert resp.status_code == 200, resp.text
    assert _stored(migrated_db_path, 910004, "valid_from") == "2026-04-13"


def test_predicate_class_change_is_refused(
    cortex_client: TestClient, migrated_db_path: Path
) -> None:
    _seed(
        migrated_db_path,
        assertion_id=910005,
        valid_from=None,
        predicate_form=_STATE_PREDICATE,
    )

    resp = cortex_client.patch(
        "/assertions/910005",
        json={"predicate_form": "denied(spread_extension, chase, 2026-04-29)"},
    )

    assert resp.status_code == 409, resp.text
    detail = resp.json()["detail"]
    assert "'status'" in detail and "'denied'" in detail
    assert _stored(migrated_db_path, 910005, "predicate_form") == _STATE_PREDICATE


def test_same_class_predicate_writeback_is_allowed(
    cortex_client: TestClient, migrated_db_path: Path
) -> None:
    _seed(
        migrated_db_path,
        assertion_id=910006,
        valid_from=None,
        predicate_form=_STATE_PREDICATE,
    )

    resp = cortex_client.patch(
        "/assertions/910006",
        json={"predicate_form": "status(account:chase-mortgage-8787, denied, current)"},
    )

    assert resp.status_code == 200, resp.text


def test_seeding_a_predicate_onto_an_empty_column_is_allowed(
    cortex_client: TestClient, migrated_db_path: Path
) -> None:
    _seed(migrated_db_path, assertion_id=910007, valid_from=None, predicate_form=None)

    resp = cortex_client.patch(
        "/assertions/910007", json={"predicate_form": _STATE_PREDICATE}
    )

    assert resp.status_code == 200, resp.text


def test_create_time_seeded_ledger_survives_a_writeback(
    cortex_client: TestClient, migrated_db_path: Path
) -> None:
    _seed(
        migrated_db_path,
        assertion_id=910008,
        valid_from=None,
        predicate_form=_STATE_PREDICATE,
        raw_predicate_form="status(chase-mortgage-8787, not_approved, current)",
    )

    resp = cortex_client.patch(
        "/assertions/910008",
        json={"predicate_form": "status(account:chase-mortgage-8787, denied, current)"},
    )

    assert resp.status_code == 200, resp.text
    assert (
        _stored(migrated_db_path, 910008, "raw_predicate_form")
        == "status(chase-mortgage-8787, not_approved, current)"
    )


@pytest.mark.parametrize("forced", [True, False])
def test_class_change_under_force_still_preserves_seeded_ledger(
    cortex_client: TestClient, migrated_db_path: Path, forced: bool
) -> None:
    assertion_id = 910009 if forced else 910010
    _seed(
        migrated_db_path,
        assertion_id=assertion_id,
        valid_from=None,
        predicate_form=_STATE_PREDICATE,
        raw_predicate_form="status(chase-mortgage-8787, seeded, current)",
    )

    payload: dict[str, object] = {
        "predicate_form": "status(account:chase-mortgage-8787, denied, current)"
    }
    if forced:
        payload["force"] = True

    resp = cortex_client.patch(f"/assertions/{assertion_id}", json=payload)

    assert resp.status_code == 200, resp.text
    assert (
        _stored(migrated_db_path, assertion_id, "raw_predicate_form")
        == "status(chase-mortgage-8787, seeded, current)"
    )
