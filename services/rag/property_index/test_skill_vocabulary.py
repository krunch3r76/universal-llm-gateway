"""Tests for per-skill vocabulary attribution (skill_vocabulary v15 migration)."""

from __future__ import annotations

from pathlib import Path

import pytest
import pytest_asyncio
from cortex_store.routes._skill_suggest_candidates import slug_from_source_uri

from services.rag.property_index import PropertyIndex
from services.rag.vocabulary._skill_attribution import build_skill_vocabulary_rows


@pytest_asyncio.fixture
async def prop_index(tmp_path: Path) -> PropertyIndex:
    idx = PropertyIndex(db_path=tmp_path / "rag_metadata.db")
    await idx.start()
    try:
        yield idx
    finally:
        await idx.stop()


@pytest.mark.asyncio
async def test_migration_v15_creates_skill_vocabulary(prop_index: PropertyIndex) -> None:
    conn = prop_index._ensure_conn()
    version = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0]
    assert version >= 15
    cols = {row[1] for row in conn.execute("PRAGMA table_info(skill_vocabulary)")}
    assert {"slug", "register", "term", "score", "chunk_count"}.issubset(cols)
    indexes = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        ).fetchall()
    }
    assert "idx_skill_vocabulary_slug" in indexes


@pytest.mark.asyncio
async def test_get_term_counts_by_source(prop_index: PropertyIndex) -> None:
    conn = prop_index._ensure_conn()
    conn.executemany(
        "INSERT INTO properties (key, chunk_id, scope, source) VALUES (?, ?, ?, ?)",
        [
            ("prop.name@@stargate", "c1", "agent_skills", "/data/agent-skills/a/SKILL.md"),
            ("prop.name@@stargate", "c2", "agent_skills", "/data/agent-skills/a/SKILL.md"),
            ("prop.topic@@gateway", "c3", "agent_skills", "/data/agent-skills/b/SKILL.md"),
        ],
    )
    conn.commit()

    rows = prop_index.get_term_counts_by_source(
        "prop.name@@", ["/data/agent-skills"]
    )
    assert rows == [
        ("/data/agent-skills/a/SKILL.md", "stargate", 2, 1),
    ]

    topic_rows = prop_index.get_term_counts_by_source(
        "prop.topic@@", ["/data/agent-skills"]
    )
    assert topic_rows == [
        ("/data/agent-skills/b/SKILL.md", "gateway", 1, 1),
    ]


@pytest.mark.asyncio
async def test_skill_vocabulary_round_trip(prop_index: PropertyIndex) -> None:
    rows = [
        ("consult-routing", "domain", "dispatch", 3.5, 4),
        ("session-close", "domain", "handoff", 2.0, 1),
    ]
    await prop_index.replace_skill_vocabulary(rows)

    assert prop_index.load_skill_vocabulary("consult-routing") == [
        ("domain", "dispatch"),
    ]
    assert len(prop_index.load_skill_vocabulary()) == 2


def test_build_skill_vocabulary_rows_join() -> None:
    scope_vocabulary = [
        ("domain", "stargate"),
        ("domain", "gateway"),
    ]
    source_term_counts = [
        ("/mnt/torus/mcp-data/files/agent-skills/foo/SKILL.md", "stargate", 3, 1),
        ("/mnt/torus/mcp-data/files/agent-skills/bar/SKILL.md", "gateway", 1, 1),
        ("/mnt/torus/mcp-data/files/agent-skills/bar/SKILL.md", "unknown", 5, 1),
    ]
    hint_scores = {"stargate": 9.0}

    rows = build_skill_vocabulary_rows(
        scope_vocabulary=scope_vocabulary,
        source_term_counts=source_term_counts,
        corpus_hint_scores=hint_scores,
        slug_from_source=slug_from_source_uri,
    )

    assert rows == [
        ("bar", "domain", "gateway", 1.0, 1),
        ("foo", "domain", "stargate", 9.0, 3),
    ]


@pytest.mark.asyncio
async def test_attribute_join_against_fixture_index(
    prop_index: PropertyIndex,
) -> None:
    conn = prop_index._ensure_conn()
    conn.executemany(
        "INSERT INTO scope_vocabulary (scope, register, term) VALUES (?, ?, ?)",
        [
            ("agent_skills", "domain", "orchestrator"),
            ("agent_skills", "domain", "dispatch"),
        ],
    )
    conn.executemany(
        "INSERT INTO corpus_hints (scope, term, score, prefix) VALUES (?, ?, ?, ?)",
        [
            ("agent_skills", "orchestrator", 8.0, "prop.name@@"),
            ("agent_skills", "dispatch", 5.0, "prop.name@@"),
        ],
    )
    conn.executemany(
        "INSERT INTO properties (key, chunk_id, scope, source) VALUES (?, ?, ?, ?)",
        [
            (
                "prop.name@@orchestrator",
                "c1",
                "agent_skills",
                "/mnt/torus/mcp-data/files/agent-skills/orchestrator-workflow/SKILL.md",
            ),
            (
                "prop.name@@dispatch",
                "c2",
                "agent_skills",
                "/mnt/torus/mcp-data/files/agent-skills/consult-routing/SKILL.md",
            ),
        ],
    )
    conn.commit()

    scope_rows = prop_index.load_scope_vocabulary_for_scope("agent_skills")
    source_counts = prop_index.get_term_counts_by_source(
        "prop.name@@", ["/mnt/torus/mcp-data/files/agent-skills"]
    )
    hint_scores = prop_index.load_corpus_hint_scores("agent_skills")
    rows = build_skill_vocabulary_rows(
        scope_vocabulary=scope_rows,
        source_term_counts=source_counts,
        corpus_hint_scores=hint_scores,
        slug_from_source=slug_from_source_uri,
    )
    await prop_index.replace_skill_vocabulary(rows)

    assert prop_index.load_skill_vocabulary("orchestrator-workflow") == [
        ("domain", "orchestrator"),
    ]
    assert prop_index.load_skill_vocabulary("consult-routing") == [
        ("domain", "dispatch"),
    ]
