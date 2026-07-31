"""Commit route — happy path, typed rejects, atomic double-commit regression."""

from __future__ import annotations

import sqlite3
import threading
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from cortex_store.life_imprint.proposal_store import create_proposal


def _seed(conn: sqlite3.Connection) -> None:
    conn.execute(
        "INSERT INTO entities (id, type, name) VALUES ('todo:ship', 'todo', 'Ship')"
    )
    conn.commit()


def _valid_patch() -> dict:
    return {
        "@context": "cortex.life/v1",
        "@graph": [{"@id": "todo:new-item", "@type": "todo", "name": "New item"}],
    }


def _propose(cortex_client: TestClient) -> str:
    resp = cortex_client.post("/graph/imprint/propose", json={"patch": _valid_patch()})
    assert resp.status_code == 200
    body = resp.json()
    assert body["proposal_id"]
    return str(body["proposal_id"])


def test_commit_happy_path_applies_plan(
    cortex_client: TestClient, migrated_conn: sqlite3.Connection
) -> None:
    _seed(migrated_conn)
    before = migrated_conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
    proposal_id = _propose(cortex_client)

    resp = cortex_client.post(
        "/graph/imprint/commit", json={"proposal_id": proposal_id}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["proposal_id"] == proposal_id
    assert body["applied"]
    assert body["context"] == "cortex.life/v1"

    after = migrated_conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
    assert after == before + 1
    row = migrated_conn.execute(
        "SELECT status, committed_at FROM imprint_proposals WHERE id = ?",
        (proposal_id,),
    ).fetchone()
    assert row["status"] == "committed"
    assert row["committed_at"]


def test_commit_sequential_double_commit_never_reapplies(
    cortex_client: TestClient, migrated_conn: sqlite3.Connection
) -> None:
    _seed(migrated_conn)
    proposal_id = _propose(cortex_client)
    before = migrated_conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0]

    first = cortex_client.post(
        "/graph/imprint/commit", json={"proposal_id": proposal_id}
    )
    assert first.status_code == 200

    second = cortex_client.post(
        "/graph/imprint/commit", json={"proposal_id": proposal_id}
    )
    assert second.status_code == 422
    body = second.json()
    assert body["code"] == "proposal_already_committed"

    after = migrated_conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
    assert after == before + 1


def test_commit_concurrent_atomic_claim_exactly_one_winner(
    cortex_client: TestClient, migrated_conn: sqlite3.Connection
) -> None:
    _seed(migrated_conn)
    proposal_id = _propose(cortex_client)
    before = migrated_conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
    barrier = threading.Barrier(2)
    results: list[tuple[int, dict]] = []

    def _attempt() -> None:
        barrier.wait()
        resp = cortex_client.post(
            "/graph/imprint/commit", json={"proposal_id": proposal_id}
        )
        results.append((resp.status_code, resp.json()))

    threads = [threading.Thread(target=_attempt) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    statuses = sorted(code for code, _ in results)
    assert statuses == [200, 422]
    codes = [body.get("code") for code, body in results if code == 422]
    assert codes == ["proposal_already_committed"]

    after = migrated_conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
    assert after == before + 1


def test_commit_unknown_proposal(cortex_client: TestClient) -> None:
    resp = cortex_client.post(
        "/graph/imprint/commit",
        json={"proposal_id": "00000000-0000-4000-8000-000000000000"},
    )
    assert resp.status_code == 422
    assert resp.json()["code"] == "unknown_proposal"


def test_commit_expired_proposal(
    cortex_client: TestClient, migrated_conn: sqlite3.Connection
) -> None:
    _seed(migrated_conn)
    proposal_id = create_proposal(
        normalized_patch={"@context": "cortex.life/v1", "@graph": []},
        op_plan=[{"op": "entity_create", "args": {"id": "todo:x", "type": "todo", "name": "X"}}],
    )
    expired = (datetime.now(UTC) - timedelta(seconds=60)).strftime("%Y-%m-%dT%H:%M:%SZ")
    migrated_conn.execute(
        "UPDATE imprint_proposals SET expires_at = ? WHERE id = ?",
        (expired, proposal_id),
    )
    migrated_conn.commit()

    resp = cortex_client.post(
        "/graph/imprint/commit", json={"proposal_id": proposal_id}
    )
    assert resp.status_code == 422
    assert resp.json()["code"] == "proposal_expired"


def test_commit_not_committable_proposal(
    cortex_client: TestClient, migrated_conn: sqlite3.Connection
) -> None:
    proposal_id = create_proposal(
        normalized_patch={"@context": "cortex.life/v1", "@graph": []},
        op_plan=[],
        rejects=[{"code": "refused_op"}],
    )
    resp = cortex_client.post(
        "/graph/imprint/commit", json={"proposal_id": proposal_id}
    )
    assert resp.status_code == 422
    assert resp.json()["code"] == "proposal_not_committable"


def test_propose_with_candidates_has_null_proposal_id(
    cortex_client: TestClient, migrated_conn: sqlite3.Connection
) -> None:
    _seed(migrated_conn)
    patch = {
        "@context": "cortex.life/v1",
        "@graph": [{"@id": "Alice", "noted": "belief"}],
    }
    resp = cortex_client.post("/graph/imprint/propose", json={"patch": patch})
    body = resp.json()
    assert body["candidates"]
    assert body["proposal_id"] is None


def test_commit_apply_failed_on_bad_plan(
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
    resp = cortex_client.post(
        "/graph/imprint/commit", json={"proposal_id": proposal_id}
    )
    assert resp.status_code == 422
    body = resp.json()
    assert body["code"] == "apply_failed"
    assert body["data"]["op_index"] == 0
    row = migrated_conn.execute(
        "SELECT status FROM imprint_proposals WHERE id = ?", (proposal_id,)
    ).fetchone()
    assert row["status"] == "open"


def test_events_module_registers_commit_factories() -> None:
    import importlib

    mod = importlib.import_module("cortex_store.events_imprint")
    for name in (
        "graph_imprint_commit_received",
        "graph_imprint_committed",
        "graph_imprint_commit_rejected",
    ):
        assert hasattr(mod, name)
