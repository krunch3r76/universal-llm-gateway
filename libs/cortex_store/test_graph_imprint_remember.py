"""Remember route — happy path, preview degrade, dedupe, concurrency, events."""

from __future__ import annotations

import sqlite3
import threading
from unittest.mock import patch

from fastapi.testclient import TestClient

from cortex_store.life_imprint.proposal_store import create_proposal


def _seed_entity(conn: sqlite3.Connection, entity_id: str, entity_type: str = "todo") -> None:
    conn.execute(
        "INSERT OR IGNORE INTO entities (id, type, name) VALUES (?, ?, ?)",
        (entity_id, entity_type, entity_id),
    )
    conn.commit()


def _insert_prior(
    conn: sqlite3.Connection,
    *,
    entity_id: str,
    claim: str,
) -> None:
    from cortex_store.claim_hash import compute_claim_hash

    claim_hash = compute_claim_hash(entity_id, claim)
    cur = conn.execute(
        "INSERT INTO assertions "
        "(entity_id, claim, confidence, evidence, claim_hash) "
        "VALUES (?, ?, 'believed', 'fixture', ?)",
        (entity_id, claim, claim_hash),
    )
    assertion_id = int(cur.lastrowid)
    conn.execute(
        "INSERT INTO assertions_fts (assertion_id, entity_id, indexed_text) VALUES (?, ?, ?)",
        (assertion_id, entity_id, claim),
    )
    conn.commit()


def _seed(conn: sqlite3.Connection) -> None:
    conn.executemany(
        "INSERT INTO entities (id, type, name) VALUES (?, ?, ?)",
        [
            ("todo:ship", "todo", "Ship"),
            ("matter:estate-2024", "matter", "Estate 2024"),
        ],
    )
    conn.commit()


def _valid_entity_patch() -> dict:
    return {
        "@context": "cortex.life/v1",
        "@graph": [{"@id": "todo:new-item", "@type": "todo", "name": "New item"}],
    }


def _relationship_patch() -> dict:
    return {
        "@context": "cortex.life/v1",
        "@graph": [
            {"@id": "todo:follow-up", "@type": "todo", "name": "Follow up"},
            {"@id": "todo:follow-up", "child_of": {"@id": "matter:estate-2024"}},
        ],
    }


def _propose(cortex_client: TestClient, patch: dict) -> dict:
    resp = cortex_client.post("/graph/imprint/propose", json={"patch": patch})
    assert resp.status_code == 200
    return resp.json()


def test_remember_happy_path_applies_plan(
    cortex_client: TestClient, migrated_conn: sqlite3.Connection
) -> None:
    _seed(migrated_conn)
    before = migrated_conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
    patch = _valid_entity_patch()

    resp = cortex_client.post("/graph/imprint/remember", json={"patch": patch})
    assert resp.status_code == 200
    body = resp.json()
    assert body["proposal_id"]
    assert body["committed"] is True
    assert body["applied"]
    assert body["deduped"] is False
    assert body["context"] == "cortex.life/v1"

    after = migrated_conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
    assert after == before + 1


def test_remember_matches_propose_commit_graph_effects(
    cortex_client: TestClient, migrated_conn: sqlite3.Connection
) -> None:
    _seed(migrated_conn)
    remember_patch = {
        "@context": "cortex.life/v1",
        "@graph": [{"@id": "todo:remember-item", "@type": "todo", "name": "Remember"}],
    }
    commit_patch = {
        "@context": "cortex.life/v1",
        "@graph": [{"@id": "todo:commit-item", "@type": "todo", "name": "Commit"}],
    }
    before = migrated_conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0]

    remember_resp = cortex_client.post(
        "/graph/imprint/remember", json={"patch": remember_patch}
    )
    assert remember_resp.status_code == 200
    remember_body = remember_resp.json()

    propose_body = _propose(cortex_client, commit_patch)
    commit_resp = cortex_client.post(
        "/graph/imprint/commit", json={"proposal_id": propose_body["proposal_id"]}
    )
    assert commit_resp.status_code == 200
    commit_body = commit_resp.json()

    after = migrated_conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
    assert after == before + 2
    assert remember_body["applied"][0]["op"] == "entity_create"
    assert commit_body["applied"][0]["op"] == "entity_create"


def test_remember_reject_preview_zero_mutations(
    cortex_client: TestClient, migrated_conn: sqlite3.Connection
) -> None:
    _seed(migrated_conn)
    patch = {
        "@context": "cortex.life/v1",
        "@graph": [{"@id": "todo:x", "delegate": "work"}],
    }
    before_entities = migrated_conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0]

    propose_body = _propose(cortex_client, patch)
    remember_resp = cortex_client.post("/graph/imprint/remember", json={"patch": patch})
    assert remember_resp.status_code == 200
    remember_body = remember_resp.json()

    assert remember_body == propose_body
    after_entities = migrated_conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
    assert after_entities == before_entities


def test_remember_candidate_preview_zero_mutations(
    cortex_client: TestClient, migrated_conn: sqlite3.Connection
) -> None:
    _seed(migrated_conn)
    patch = {
        "@context": "cortex.life/v1",
        "@graph": [{"@id": "Alice", "noted": "belief"}],
    }
    before_entities = migrated_conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0]

    propose_body = _propose(cortex_client, patch)
    remember_resp = cortex_client.post("/graph/imprint/remember", json={"patch": patch})
    assert remember_resp.status_code == 200
    remember_body = remember_resp.json()

    assert remember_body == propose_body
    assert remember_body["proposal_id"] is None
    after_entities = migrated_conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
    assert after_entities == before_entities


def test_remember_dedupe_within_window(
    cortex_client: TestClient, migrated_conn: sqlite3.Connection
) -> None:
    _seed(migrated_conn)
    patch = _valid_entity_patch()
    before = migrated_conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0]

    first = cortex_client.post("/graph/imprint/remember", json={"patch": patch})
    assert first.status_code == 200
    first_body = first.json()
    assert first_body["deduped"] is False

    second = cortex_client.post("/graph/imprint/remember", json={"patch": patch})
    assert second.status_code == 200
    second_body = second.json()
    assert second_body["deduped"] is True
    assert second_body["proposal_id"] == first_body["proposal_id"]
    assert second_body["applied"] == first_body["applied"]

    after = migrated_conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
    assert after == before + 1


def test_remember_apply_failed_leaves_open_proposal(
    cortex_client: TestClient, migrated_conn: sqlite3.Connection
) -> None:
    proposal_id = create_proposal(
        normalized_patch={"@context": "cortex.life/v1", "@graph": []},
        op_plan=[
            {
                "op": "entity_update",
                "args": {"entity_id": "todo:missing", "attributes": {"priority": "high"}},
            }
        ],
    )
    fail_resp = cortex_client.post(
        "/graph/imprint/commit", json={"proposal_id": proposal_id}
    )
    assert fail_resp.status_code == 422
    body = fail_resp.json()
    assert body["code"] == "apply_failed"
    row = migrated_conn.execute(
        "SELECT status FROM imprint_proposals WHERE id = ?", (proposal_id,)
    ).fetchone()
    assert row["status"] == "open"

    # Recovery: commit path remains available for the open proposal id.
    retry = cortex_client.post(
        "/graph/imprint/commit", json={"proposal_id": proposal_id}
    )
    assert retry.status_code == 422
    assert retry.json()["code"] == "apply_failed"


def test_remember_concurrent_relationship_create_single_effect(
    cortex_client: TestClient, migrated_conn: sqlite3.Connection
) -> None:
    _seed(migrated_conn)
    patch = _relationship_patch()
    before_entities = migrated_conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
    before_rels = migrated_conn.execute("SELECT COUNT(*) FROM relationships").fetchone()[0]
    barrier = threading.Barrier(2)
    results: list[tuple[int, dict]] = []

    def _attempt() -> None:
        barrier.wait()
        resp = cortex_client.post("/graph/imprint/remember", json={"patch": patch})
        results.append((resp.status_code, resp.json()))

    threads = [threading.Thread(target=_attempt) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert all(code == 200 for code, _ in results)
    proposal_ids = {body["proposal_id"] for _, body in results}
    assert len(proposal_ids) == 1
    deduped_flags = {body.get("deduped") for _, body in results}
    assert deduped_flags == {True, False}

    after_entities = migrated_conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
    after_rels = migrated_conn.execute("SELECT COUNT(*) FROM relationships").fetchone()[0]
    assert after_entities == before_entities + 1
    assert after_rels == before_rels + 1


def test_remember_success_emits_only_remember_events(
    cortex_client: TestClient, migrated_conn: sqlite3.Connection
) -> None:
    _seed(migrated_conn)
    captured: list[str] = []

    def _capture(signal: str, **_: object) -> None:
        captured.append(signal)

    with patch("cortex_store.events_imprint.record", side_effect=_capture):
        resp = cortex_client.post(
            "/graph/imprint/remember", json={"patch": _valid_entity_patch()}
        )
    assert resp.status_code == 200

    assert "graph.imprint.remember.received" in captured
    assert "graph.imprint.remembered" in captured
    assert "graph.imprint.proposed" not in captured
    assert "graph.imprint.commit.received" not in captured
    assert "graph.imprint.committed" not in captured
    assert "graph.imprint.commit.rejected" not in captured


def test_remember_already_known_skips_second_assertion_row(
    cortex_client: TestClient,
    migrated_conn: sqlite3.Connection,
) -> None:
    _seed_entity(migrated_conn, "todo:ship")
    anchor = "2026-07-17#overnight-rideshare-micro-sleeps"
    claim = f"{anchor}: blocked on review cycle."
    _insert_prior(migrated_conn, entity_id="todo:ship", claim=claim)
    life_patch = {
        "@context": "cortex.life/v1",
        "@graph": [{"@id": "todo:ship", "noted": claim}],
    }
    before = migrated_conn.execute("SELECT COUNT(*) FROM assertions").fetchone()[0]
    captured: list[str] = []

    def _capture(signal: str, **_: object) -> None:
        captured.append(signal)

    with patch("cortex_store.events_imprint.record", side_effect=_capture):
        resp = cortex_client.post("/graph/imprint/remember", json={"patch": life_patch})
    assert resp.status_code == 200
    body = resp.json()
    assert body["already_known"] is True
    assert body["deduped"] is False
    assert body["committed"] is False
    after = migrated_conn.execute("SELECT COUNT(*) FROM assertions").fetchone()[0]
    assert after == before
    assert "graph.recorder.already_known" in captured
    assert any(
        ev == "graph.imprint.remembered" for ev in captured
    )


def test_events_module_registers_remember_factories() -> None:
    import importlib

    mod = importlib.import_module("cortex_store.events_imprint")
    for name in (
        "graph_imprint_remember_received",
        "graph_imprint_remembered",
        "graph_imprint_remember_rejected",
    ):
        assert hasattr(mod, name)
