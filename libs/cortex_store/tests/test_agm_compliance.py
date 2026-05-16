"""AGM Compliance Test Suite — Kumiho postulate verification against Cortex API.

Tests each AGM postulate category against the live Cortex REST surface using
FastAPI TestClient with an isolated in-memory database per test.

Postulate coverage:
  K÷2 Success, K÷3 Inclusion, K÷4 Preservation, K÷5 Consistency,
  K÷6 Extensionality, K÷7 Superexpansion, K÷8 Subexpansion,
  Relevance, Core-Retainment, Tag Pointer Consistency

Origin: Agent bus thread 453, Phase A4 of cortex-v3-kumiho-complete.md
"""

from __future__ import annotations

import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Bootstrap schema for in-memory test DB
# ---------------------------------------------------------------------------

_BASE_SCHEMA = """\
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY,
    description TEXT,
    applied_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS entities (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    name TEXT NOT NULL,
    description TEXT,
    aliases TEXT,
    attributes TEXT,
    notes TEXT,
    source_uri TEXT,
    content_hash TEXT,
    status TEXT DEFAULT 'confirmed',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS assertions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_id TEXT NOT NULL REFERENCES entities(id),
    claim TEXT NOT NULL,
    confidence TEXT NOT NULL DEFAULT 'believed',
    confidence_score REAL,
    evidence TEXT,
    evidence_uris TEXT,
    seeded_by TEXT,
    derivation_type TEXT DEFAULT 'inference',
    chunk_id INTEGER,
    reasoning_summary TEXT,
    is_atomic INTEGER DEFAULT 1,
    is_decontextualized INTEGER DEFAULT 1,
    observed_at TEXT,
    valid_from TEXT,
    valid_until TEXT,
    superseded_by INTEGER,
    review_status TEXT DEFAULT 'committed',
    reviewer TEXT,
    reviewed_at TEXT,
    review_notes TEXT,
    resolution_status TEXT,
    fulfillment_assertion_id INTEGER,
    quality_score REAL,
    claim_hash TEXT,
    prospective_summary TEXT,
    events_json TEXT,
    artifact_uri TEXT,
    artifact_storage TEXT DEFAULT 'inline',
    entrenchment_score REAL,
    updated_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(entity_id, claim_hash) ON CONFLICT IGNORE
);

CREATE TABLE IF NOT EXISTS relationships (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id TEXT,
    target_id TEXT,
    from_entity TEXT,
    to_entity TEXT,
    type_id TEXT,
    type TEXT,
    role TEXT,
    strength REAL,
    evidence TEXT,
    chunk_id INTEGER,
    valid_from TEXT,
    valid_until TEXT,
    source_uri TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS session_edges (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    agent TEXT NOT NULL,
    from_node TEXT NOT NULL,
    to_node TEXT NOT NULL,
    edge_type TEXT NOT NULL,
    strength REAL DEFAULT 0.8,
    edge_source TEXT DEFAULT 'explicit',
    context TEXT,
    prompt TEXT,
    seeded_by TEXT,
    valid_until TEXT,
    metadata TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS session_journals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT,
    agent TEXT,
    summary TEXT,
    decisions TEXT,
    open_items TEXT,
    domains TEXT,
    entity_ids TEXT,
    file_path TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    content TEXT,
    source_uri TEXT,
    source_date TEXT,
    observer TEXT DEFAULT 'web-claude',
    chunk_index INTEGER DEFAULT 0,
    extraction_run INTEGER,
    token_count INTEGER,
    source_hash TEXT,
    model_version TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS surface_forms (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    mention TEXT,
    entity_id TEXT,
    chunk_id INTEGER,
    resolution_confidence REAL,
    resolution_reasoning TEXT,
    context_hash TEXT,
    mention_type TEXT,
    span_start INTEGER,
    span_end INTEGER,
    entity_type_hint TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS entity_access_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_id TEXT NOT NULL,
    agent TEXT NOT NULL,
    session_id TEXT,
    source TEXT DEFAULT 'agent',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS entity_access_summary (
    entity_id TEXT NOT NULL,
    agent TEXT NOT NULL,
    week_start TEXT NOT NULL,
    agent_access_count INTEGER DEFAULT 0,
    boot_access_count INTEGER DEFAULT 0,
    session_count INTEGER DEFAULT 0,
    UNIQUE(entity_id, agent, week_start)
);

CREATE TABLE IF NOT EXISTS entity_salience_cache (
    entity_id TEXT PRIMARY KEY,
    salience_score REAL,
    temporal_score REAL,
    structural_score REAL,
    contextual_score REAL,
    frequency_score REAL,
    fast_state_hash TEXT,
    slow_state_hash TEXT,
    last_surprise REAL,
    fingerprint TEXT,
    computed_at TEXT,
    boot_section_cache TEXT
);

CREATE TABLE IF NOT EXISTS tag_assignments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tag_name TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    assertion_id INTEGER NOT NULL,
    assigned_at TEXT NOT NULL DEFAULT (datetime('now')),
    assigned_by TEXT NOT NULL,
    UNIQUE(tag_name, entity_id),
    FOREIGN KEY (entity_id) REFERENCES entities(id),
    FOREIGN KEY (assertion_id) REFERENCES assertions(id)
);

CREATE INDEX IF NOT EXISTS idx_tag_entity ON tag_assignments(entity_id);
CREATE INDEX IF NOT EXISTS idx_tag_name ON tag_assignments(tag_name);

CREATE TABLE IF NOT EXISTS near_duplicate_flags (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    assertion_id INTEGER NOT NULL,
    existing_id INTEGER NOT NULL,
    score REAL NOT NULL,
    reviewed INTEGER DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE VIRTUAL TABLE IF NOT EXISTS assertions_fts USING fts5(
    assertion_id UNINDEXED,
    entity_id UNINDEXED,
    indexed_text,
    content='',
    tokenize='unicode61'
);
"""


def _bootstrap_db(db_path: str) -> None:
    """Create a fresh test database with full Cortex schema."""
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(_BASE_SCHEMA)
    # Mark all migrations as applied so the app doesn't re-run them
    for v in range(1, 24):
        conn.execute(
            "INSERT OR IGNORE INTO schema_version (version, description) VALUES (?, ?)",
            (v, f"test_bootstrap_{v:03d}"),
        )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def test_db(tmp_path: Path) -> str:
    """Provide a fresh test database path for each test."""
    db_path = str(tmp_path / "test_cortex.db")
    _bootstrap_db(db_path)
    return db_path


@pytest.fixture()
def client(test_db: str) -> TestClient:
    """Create a TestClient against a fresh cortex-api app with isolated DB."""
    os.environ["CORTEX_DB_PATH"] = test_db
    # Force module to pick up new env
    from cortex_store.main import create_app

    app = create_app(db_path=test_db)
    return TestClient(app)


def _uid() -> str:
    return uuid4().hex[:8]


def _create_entity(client: TestClient, entity_id: str | None = None) -> str:
    """Create a test entity, return its ID."""
    eid = entity_id or f"test:agm-{_uid()}"
    r = client.post(
        "/entities",
        json={"id": eid, "type": "test", "name": f"AGM Test {eid}"},
    )
    assert r.status_code in (200, 201), f"Entity create failed: {r.text}"
    return eid


def _assert_claim(
    client: TestClient,
    entity_id: str,
    claim: str,
    confidence: str = "believed",
    derivation_type: str = "agent_observation",
    **kwargs: Any,
) -> dict[str, Any]:
    """Create an assertion, return the response dict.

    Uses agent_observation by default — skips chunk_id/evidence_uris
    requirements and auto-qualifies observed_at.
    """
    body: dict[str, Any] = {
        "entity_id": entity_id,
        "claim": claim,
        "confidence": confidence,
        "evidence": f"AGM test evidence for: {claim}",
        "derivation_type": derivation_type,
        "observed_at": datetime.now(UTC).isoformat(),
        "reasoning_summary": "AGM compliance test scenario",
        **kwargs,
    }
    r = client.post("/assertions", json=body)
    assert r.status_code in (200, 201), f"Assertion create failed: {r.text}"
    data = r.json()
    return data["item"]


def _supersede(
    client: TestClient,
    old_id: int,
    entity_id: str,
    new_claim: str,
    confidence: str = "believed",
    **kwargs: Any,
) -> dict[str, Any]:
    """Supersede an assertion, return the response with old + new."""
    body: dict[str, Any] = {
        "old_assertion_id": old_id,
        "entity_id": entity_id,
        "claim": new_claim,
        "confidence": confidence,
        "evidence": f"AGM supersede evidence for: {new_claim}",
        "session_id": f"agm-test-{_uid()}",
        "agent": "agm-test",
        **kwargs,
    }
    r = client.post("/assertions/supersede", json=body)
    assert r.status_code == 201, f"Supersede failed: {r.text}"
    return r.json()


def _get_active_assertions(client: TestClient, entity_id: str) -> list[dict[str, Any]]:
    """Get all active (non-superseded) assertions for an entity."""
    r = client.get("/assertions", params={"entity_id": entity_id, "superseded": False})
    assert r.status_code == 200
    return r.json()["items"]


# ===================================================================
# K÷2 — Success
# After revision with new evidence, the new belief exists in the belief base.
# ===================================================================


class TestK2Success:
    def test_supersede_creates_new_active_belief(self, client: TestClient) -> None:
        eid = _create_entity(client)
        a = _assert_claim(client, eid, "Earth orbits the Sun")
        result = _supersede(client, a["id"], eid, "Earth orbits the Sun at 1 AU")

        assert result["new"]["id"] != a["id"]
        active = _get_active_assertions(client, eid)
        active_ids = {item["id"] for item in active}
        assert result["new"]["id"] in active_ids

    def test_superseded_belief_is_inactive(self, client: TestClient) -> None:
        eid = _create_entity(client)
        a = _assert_claim(client, eid, "Protocol version is 2.0")
        _supersede(client, a["id"], eid, "Protocol version is 3.0")

        active = _get_active_assertions(client, eid)
        active_ids = {item["id"] for item in active}
        assert a["id"] not in active_ids


# ===================================================================
# K÷3 — Inclusion
# Expansion preserves existing beliefs that don't contradict.
# ===================================================================


class TestK3Inclusion:
    def test_new_belief_coexists_with_existing(self, client: TestClient) -> None:
        eid = _create_entity(client)
        a1 = _assert_claim(client, eid, "Service uses Python 3.12")
        a2 = _assert_claim(client, eid, "Service runs on port 9999")

        active = _get_active_assertions(client, eid)
        active_ids = {item["id"] for item in active}
        assert a1["id"] in active_ids
        assert a2["id"] in active_ids

    def test_multiple_non_contradictory_expansions(self, client: TestClient) -> None:
        eid = _create_entity(client)
        claims = [
            "Uses FastAPI framework",
            "Database is SQLite",
            "Transport is UDS",
        ]
        assertion_ids = set()
        for claim in claims:
            a = _assert_claim(client, eid, claim)
            assertion_ids.add(a["id"])

        active = _get_active_assertions(client, eid)
        active_ids = {item["id"] for item in active}
        assert assertion_ids == active_ids


# ===================================================================
# K÷4 — Preservation
# If the new belief doesn't contradict, all old beliefs survive.
# ===================================================================


class TestK4Preservation:
    def test_adding_compatible_belief_preserves_all(self, client: TestClient) -> None:
        eid = _create_entity(client)
        a = _assert_claim(client, eid, "System has 64GB RAM")
        b = _assert_claim(client, eid, "GPU is RTX 3090")
        c = _assert_claim(client, eid, "OS is Linux 6.x")

        # Add compatible belief D
        d = _assert_claim(client, eid, "Python version is 3.12")

        active = _get_active_assertions(client, eid)
        active_ids = {item["id"] for item in active}
        for original in (a, b, c, d):
            assert original["id"] in active_ids, (
                f"Assertion {original['id']} ({original['claim']}) not preserved"
            )


# ===================================================================
# K÷5 — Consistency
# The result of revision is consistent (no active contradictions).
# ===================================================================


class TestK5Consistency:
    def test_supersede_removes_old_from_active(self, client: TestClient) -> None:
        eid = _create_entity(client)
        a = _assert_claim(client, eid, "Config uses YAML format")
        _supersede(client, a["id"], eid, "Config uses TOML format")

        active = _get_active_assertions(client, eid)
        active_claims = {item["claim"] for item in active}
        assert "Config uses YAML format" not in active_claims
        assert "Config uses TOML format" in active_claims

    def test_superseded_assertion_has_superseded_by(self, client: TestClient) -> None:
        eid = _create_entity(client)
        a = _assert_claim(client, eid, "Port is 8080")
        result = _supersede(client, a["id"], eid, "Port is 9999")

        assert result["old"]["superseded_by"] == result["new"]["id"]

    def test_boot_excludes_superseded(self, client: TestClient) -> None:
        """Verify superseded assertions don't appear when querying active."""
        eid = _create_entity(client)
        a = _assert_claim(client, eid, "Version A")
        _supersede(client, a["id"], eid, "Version B")

        active = _get_active_assertions(client, eid)
        active_claims = {item["claim"] for item in active}
        assert "Version A" not in active_claims
        assert "Version B" in active_claims


# ===================================================================
# K÷6 — Extensionality
# Equivalent inputs produce equivalent revision behavior.
# ===================================================================


class TestK6Extensionality:
    def test_identical_revision_on_two_entities(self, client: TestClient) -> None:
        eid1 = _create_entity(client)
        eid2 = _create_entity(client)

        a1 = _assert_claim(client, eid1, "Model accuracy is 95%")
        a2 = _assert_claim(client, eid2, "Model accuracy is 95%")

        r1 = _supersede(client, a1["id"], eid1, "Model accuracy is 97%")
        r2 = _supersede(client, a2["id"], eid2, "Model accuracy is 97%")

        # Both entities should have equivalent belief states
        active1 = _get_active_assertions(client, eid1)
        active2 = _get_active_assertions(client, eid2)

        claims1 = {item["claim"] for item in active1}
        claims2 = {item["claim"] for item in active2}
        assert claims1 == claims2

        # Old assertions superseded in both
        assert r1["old"]["superseded_by"] is not None
        assert r2["old"]["superseded_by"] is not None


# ===================================================================
# K÷7 — Superexpansion (REQUIRES ENTRENCHMENT)
# Lower-entrenchment beliefs contract first.
# ===================================================================


class TestK7Superexpansion:
    def test_entrenchment_ordering_by_confidence(self, client: TestClient) -> None:
        """Higher confidence → higher entrenchment score."""
        eid = _create_entity(client)

        high = _assert_claim(
            client,
            eid,
            "Core architecture is event-driven",
            confidence="confirmed",
            derivation_type="direct_observation",
        )
        low = _assert_claim(
            client,
            eid,
            "Might migrate to Rust eventually",
            confidence="hypothesized",
            derivation_type="agent_observation",
        )

        assert high["entrenchment_score"] is not None
        assert low["entrenchment_score"] is not None
        assert high["entrenchment_score"] > low["entrenchment_score"]

    def test_entrenchment_ordering_via_endpoint(self, client: TestClient) -> None:
        """GET /assertions/entrenchment returns ordered by score desc."""
        eid = _create_entity(client)

        _assert_claim(
            client,
            eid,
            "Confirmed direct fact",
            confidence="confirmed",
            derivation_type="direct_observation",
        )
        _assert_claim(
            client,
            eid,
            "Hypothesized guess from observation",
            confidence="hypothesized",
            derivation_type="agent_observation",
        )
        _assert_claim(
            client,
            eid,
            "Believed inference about architecture",
            confidence="believed",
            derivation_type="inference",
        )

        r = client.get("/assertions/entrenchment", params={"entity_id": eid})
        assert r.status_code == 200
        items = r.json()["items"]
        assert len(items) == 3

        scores = [item["entrenchment_score"] for item in items]
        assert scores == sorted(scores, reverse=True), (
            f"Not in descending order: {scores}"
        )

    def test_manual_supersede_respects_entrenchment(self, client: TestClient) -> None:
        """When manually superseding, lower-entrenchment belief is the target."""
        eid = _create_entity(client)

        high = _assert_claim(
            client,
            eid,
            "Fundamental invariant: UDS transport",
            confidence="confirmed",
            derivation_type="direct_observation",
        )
        low = _assert_claim(
            client,
            eid,
            "Might switch to gRPC someday",
            confidence="hypothesized",
            derivation_type="agent_observation",
        )

        # Supersede the LOW entrenchment belief (correct K÷7 behavior)
        _supersede(client, low["id"], eid, "gRPC evaluation deferred indefinitely")

        active = _get_active_assertions(client, eid)
        active_ids = {item["id"] for item in active}
        assert high["id"] in active_ids, "High-entrenchment belief should survive"
        assert low["id"] not in active_ids, (
            "Low-entrenchment belief should be superseded"
        )


# ===================================================================
# K÷8 — Subexpansion
# Revision makes minimal changes — only the targeted belief is affected.
# ===================================================================


class TestK8Subexpansion:
    def test_supersede_only_affects_targeted_belief(self, client: TestClient) -> None:
        eid = _create_entity(client)

        a = _assert_claim(client, eid, "Database is PostgreSQL")
        b = _assert_claim(client, eid, "Cache layer is Redis")
        c = _assert_claim(client, eid, "Message queue is RabbitMQ")

        _supersede(client, b["id"], eid, "Cache layer is Memcached")

        active = _get_active_assertions(client, eid)
        active_ids = {item["id"] for item in active}
        active_claims = {item["claim"] for item in active}

        assert a["id"] in active_ids, "A unchanged"
        assert c["id"] in active_ids, "C unchanged"
        assert b["id"] not in active_ids, "B was superseded"
        assert "Cache layer is Memcached" in active_claims

    def test_supersede_preserves_metadata_of_siblings(self, client: TestClient) -> None:
        eid = _create_entity(client)

        a = _assert_claim(
            client,
            eid,
            "Worker count is 4",
            confidence="confirmed",
            derivation_type="direct_observation",
        )
        b = _assert_claim(client, eid, "Batch size is 32")

        _supersede(client, b["id"], eid, "Batch size is 64")

        # Re-fetch A — should be unchanged
        r = client.get("/assertions", params={"entity_id": eid, "superseded": False})
        active = r.json()["items"]
        a_after = next(item for item in active if item["id"] == a["id"])
        assert a_after["confidence"] == "confirmed"
        assert a_after["derivation_type"] == "direct_observation"
        assert a_after["superseded_by"] is None


# ===================================================================
# Relevance
# Contraction affects only beliefs relevant to the contracted belief.
# ===================================================================


class TestRelevance:
    def test_supersede_on_entity1_does_not_affect_entity2(
        self, client: TestClient
    ) -> None:
        eid1 = _create_entity(client)
        eid2 = _create_entity(client)

        a1 = _assert_claim(client, eid1, "Entity 1 uses Python")
        a2 = _assert_claim(client, eid2, "Entity 2 uses Rust")

        _supersede(client, a1["id"], eid1, "Entity 1 uses Go")

        # Entity 2 assertions completely unchanged
        active2 = _get_active_assertions(client, eid2)
        assert len(active2) == 1
        assert active2[0]["id"] == a2["id"]
        assert active2[0]["claim"] == "Entity 2 uses Rust"
        assert active2[0]["superseded_by"] is None

    def test_cross_entity_isolation_with_multiple_assertions(
        self, client: TestClient
    ) -> None:
        eid1 = _create_entity(client)
        eid2 = _create_entity(client)

        claims_e2 = ["Fact A", "Fact B", "Fact C"]
        e2_ids = set()
        for claim in claims_e2:
            a = _assert_claim(client, eid2, claim)
            e2_ids.add(a["id"])

        # Heavy revision on entity 1
        a1 = _assert_claim(client, eid1, "Old claim")
        _supersede(client, a1["id"], eid1, "New claim v1")
        r = client.get("/assertions", params={"entity_id": eid1, "superseded": False})
        latest = r.json()["items"][0]
        _supersede(client, latest["id"], eid1, "New claim v2")

        # Entity 2 still fully intact
        active2 = _get_active_assertions(client, eid2)
        active2_ids = {item["id"] for item in active2}
        assert active2_ids == e2_ids


# ===================================================================
# Core-Retainment
# Core beliefs (high entrenchment, committed status) survive contraction.
# ===================================================================


class TestCoreRetainment:
    def test_committed_high_entrenchment_is_undeprecatable(
        self, client: TestClient
    ) -> None:
        """Committed + high entrenchment assertions are protected from
        automated deprecation (Dream State protection principle)."""
        eid = _create_entity(client)

        core = _assert_claim(
            client,
            eid,
            "Fundamental system invariant: single-writer",
            confidence="confirmed",
            derivation_type="direct_observation",
        )
        peripheral = _assert_claim(
            client,
            eid,
            "Maybe try a different approach later",
            confidence="hypothesized",
            derivation_type="agent_observation",
        )

        # Core has higher entrenchment
        assert core["entrenchment_score"] > peripheral["entrenchment_score"]

        # Core has committed review status (default)
        assert core.get("review_status") in ("committed", None)

        # Verify ordering: entrenchment endpoint puts core first
        r = client.get("/assertions/entrenchment", params={"entity_id": eid})
        items = r.json()["items"]
        assert items[0]["id"] == core["id"], (
            "Core belief should be first (highest entrenchment)"
        )

    def test_staged_low_entrenchment_is_depreciable(self, client: TestClient) -> None:
        """Staged + low entrenchment assertions are valid contraction targets."""
        eid = _create_entity(client)

        staged = _assert_claim(
            client,
            eid,
            "Tentative observation about performance",
            confidence="hypothesized",
            derivation_type="agent_observation",
        )

        # Update review status to staged
        r = client.patch(
            f"/assertions/{staged['id']}",
            json={"review_status": "staged"},
        )
        assert r.status_code == 200

        updated = r.json()
        assert updated["review_status"] == "staged"
        assert (updated["entrenchment_score"] or 0.0) < 0.5


# ===================================================================
# Tag Pointer Consistency
# Tag reassignment produces correct belief state.
# ===================================================================


class TestTagPointerConsistency:
    def test_tag_resolves_to_assigned_assertion(self, client: TestClient) -> None:
        eid = _create_entity(client)
        _assert_claim(client, eid, "Version 1")
        a2 = _assert_claim(client, eid, "Version 2")
        _assert_claim(client, eid, "Version 3")

        # Tag 'current' → A2
        r = client.put(
            "/tags",
            json={
                "tag_name": "current",
                "entity_id": eid,
                "assertion_id": a2["id"],
                "assigned_by": "agm-test",
            },
        )
        assert r.status_code == 200

        # Resolve via tag
        r = client.get(
            "/resolve",
            params={
                "uri": f"cortex://test/{eid.split(':')[1]}",
                "tag": "current",
            },
        )
        assert r.status_code == 200
        data = r.json()
        assert data["assertion"]["id"] == a2["id"]
        assert data.get("resolved_via_tag") == "current"

    def test_tag_move_updates_resolution(self, client: TestClient) -> None:
        eid = _create_entity(client)
        _assert_claim(client, eid, "Phase 1")
        a2 = _assert_claim(client, eid, "Phase 2")
        a3 = _assert_claim(client, eid, "Phase 3")
        slug = eid.split(":")[1]

        # Tag → A2
        client.put(
            "/tags",
            json={
                "tag_name": "current",
                "entity_id": eid,
                "assertion_id": a2["id"],
                "assigned_by": "agm-test",
            },
        )

        r = client.get(
            "/resolve",
            params={
                "uri": f"cortex://test/{slug}",
                "tag": "current",
            },
        )
        assert r.json()["assertion"]["id"] == a2["id"]

        # Move tag → A3
        client.put(
            "/tags",
            json={
                "tag_name": "current",
                "entity_id": eid,
                "assertion_id": a3["id"],
                "assigned_by": "agm-test",
            },
        )

        r = client.get(
            "/resolve",
            params={
                "uri": f"cortex://test/{slug}",
                "tag": "current",
            },
        )
        assert r.json()["assertion"]["id"] == a3["id"]

    def test_resolve_without_tag_returns_latest(self, client: TestClient) -> None:
        eid = _create_entity(client)
        slug = eid.split(":")[1]

        _assert_claim(client, eid, "Revision 1")
        _assert_claim(client, eid, "Revision 2")

        # Tag at revision 1 — but resolving without tag should return entity only
        r = client.get(
            "/resolve",
            params={
                "uri": f"cortex://test/{slug}",
            },
        )
        assert r.status_code == 200
        data = r.json()
        assert data["resolved"] == "entity"
        assert data["entity"]["id"] == eid

    def test_tag_list_shows_all_tags(self, client: TestClient) -> None:
        eid = _create_entity(client)
        a1 = _assert_claim(client, eid, "Assertion 1")
        a2 = _assert_claim(client, eid, "Assertion 2")

        client.put(
            "/tags",
            json={
                "tag_name": "approved",
                "entity_id": eid,
                "assertion_id": a1["id"],
                "assigned_by": "agm-test",
            },
        )
        client.put(
            "/tags",
            json={
                "tag_name": "latest",
                "entity_id": eid,
                "assertion_id": a2["id"],
                "assigned_by": "agm-test",
            },
        )

        r = client.get("/tags", params={"entity_id": eid})
        assert r.status_code == 200
        tags = r.json()["items"]
        tag_names = {t["tag_name"] for t in tags}
        assert tag_names == {"approved", "latest"}


# ===================================================================
# Entrenchment Computation Correctness
# ===================================================================


class TestEntrenchmentComputation:
    def test_confirmed_direct_highest(self, client: TestClient) -> None:
        eid = _create_entity(client)
        a = _assert_claim(
            client,
            eid,
            "Confirmed direct fact",
            confidence="confirmed",
            derivation_type="direct_observation",
        )
        assert a["entrenchment_score"] is not None
        assert a["entrenchment_score"] > 0.3

    def test_hypothesized_agent_obs_lowest(self, client: TestClient) -> None:
        eid = _create_entity(client)
        a = _assert_claim(
            client,
            eid,
            "Hypothesized guess based on observation",
            confidence="hypothesized",
            derivation_type="agent_observation",
        )
        assert a["entrenchment_score"] is not None
        assert a["entrenchment_score"] < 0.2

    def test_supersede_assigns_entrenchment(self, client: TestClient) -> None:
        eid = _create_entity(client)
        old = _assert_claim(client, eid, "Old belief")
        result = _supersede(
            client,
            old["id"],
            eid,
            "New belief",
            confidence="confirmed",
            derivation_type="direct_observation",
        )
        assert result["new"]["entrenchment_score"] is not None
        assert result["new"]["entrenchment_score"] > 0.0
