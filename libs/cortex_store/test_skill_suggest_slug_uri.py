"""slug_from_source_uri + authoritative suggestion envelope tests (SF1)."""

from __future__ import annotations

import json
import sqlite3
from unittest.mock import patch

import pytest

from cortex_store.routes._skill_suggest import run_stage_a, slug_from_source_uri

_SCHEMA = """
CREATE TABLE entities (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    name TEXT NOT NULL,
    description TEXT,
    source_uri TEXT,
    lifecycle TEXT,
    attributes TEXT
);
"""


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    return conn


def _insert(
    conn: sqlite3.Connection,
    entity_id: str,
    *,
    source_uri: str,
    trigger_match_terms: list[str],
    applicable_agents: list[str] | None = None,
) -> None:
    agents = applicable_agents if applicable_agents is not None else ["claude-web"]
    conn.execute(
        "INSERT INTO entities (id, type, name, source_uri, lifecycle, attributes) "
        "VALUES (?, 'agent_skill', ?, ?, 'active', ?)",
        (
            entity_id,
            entity_id.removeprefix("agent_skill:"),
            source_uri,
            json.dumps(
                {
                    "applicable_agents": agents,
                    "trigger_match_terms": trigger_match_terms,
                }
            ),
        ),
    )


@pytest.mark.offline
@pytest.mark.parametrize(
    ("source_uri", "expected_slug"),
    [
        # bare token (clean rows — must stay unchanged)
        ("agent-skills/consult-routing.md", "consult-routing"),
        ("consult-routing.md", "consult-routing"),
        ("consult-routing", "consult-routing"),
        # double-scheme: cortex:// glued onto an already cortex-schemed slug
        (
            "cortex://agent-skills/consensus-steelman-posture.md",
            "consensus-steelman-posture",
        ),
        # full workspaces path carried as the slug
        (
            "workspaces://universal-llm-gateway/docs/agent-guides/skills/handoff-packet-authoring.md",
            "handoff-packet-authoring",
        ),
        # malformed scheme without // (cortex:agent-skills/…)
        ("cortex:agent-skills/cursor-rule-authoring.md", "cursor-rule-authoring"),
        # directory-layout skills — body at <slug>/SKILL.md; slug is the parent
        # dir, NOT the convention marker "SKILL" (friction 17551).
        (
            "workspaces://universal-llm-gateway/.cursor/skills/add-mcp-tool/SKILL.md",
            "add-mcp-tool",
        ),
        ("agent-skills/add-mcp-tool/SKILL.md", "add-mcp-tool"),
        ("cortex://agent-skills/add-mcp-tool/SKILL.md", "add-mcp-tool"),
        (
            "workspaces://universal-llm-gateway/.cursor/skills/agent-bus-multitask/SKILL.md",
            "agent-bus-multitask",
        ),
        # README marker + case-insensitive stem match
        (
            "workspaces://universal-llm-gateway/.cursor/skills/build-pipeline/README.md",
            "build-pipeline",
        ),
        ("agent-skills/service-lifecycle/skill.md", "service-lifecycle"),
        # degenerate: bare convention marker with no parent → falls through to stem
        ("SKILL.md", "SKILL"),
        (None, None),
        ("", None),
    ],
)
def test_slug_is_bare_token(source_uri: str | None, expected_slug: str | None) -> None:
    assert slug_from_source_uri(source_uri) == expected_slug


@pytest.mark.offline
def test_directory_layout_suggestions_emit_authoritative_source_uri() -> None:
    """SF1: suggestions must carry entity source_uri, not slug-derived cortex://…/.md."""
    sources = {
        "add-mcp-tool": (
            "workspaces://universal-llm-gateway/.cursor/skills/add-mcp-tool/SKILL.md"
        ),
        "agent-bus-multitask": (
            "workspaces://universal-llm-gateway/.cursor/skills/agent-bus-multitask/SKILL.md"
        ),
        "build-pipeline": (
            "workspaces://universal-llm-gateway/.cursor/skills/build-pipeline/SKILL.md"
        ),
    }
    conn = _conn()
    for slug, source_uri in sources.items():
        _insert(
            conn,
            f"agent_skill:{slug}",
            source_uri=source_uri,
            trigger_match_terms=[slug.replace("-", "_"), slug],
        )
    conn.commit()

    with patch("cortex_store.routes._skill_suggest.cortex_conn", return_value=conn):
        result = run_stage_a(
            agent="claude-web",
            loaded=[],
            conversation_context="add-mcp-tool agent-bus-multitask build-pipeline",
            limit=8,
        )

    by_slug = {s["slug"]: s for s in result["suggestions"]}
    assert set(by_slug) == set(sources)
    for slug, source_uri in sources.items():
        item = by_slug[slug]
        assert item["source_uri"] == source_uri
        assert "uri" not in item
        assert "digest" in item


@pytest.mark.offline
def test_flat_file_suggestion_emits_entity_source_uri_not_slug_derived_uri() -> None:
    conn = _conn()
    _insert(
        conn,
        "agent_skill:consult-routing",
        source_uri="agent-skills/consult-routing.md",
        trigger_match_terms=["consult", "routing"],
        applicable_agents=["claude-cursor"],
    )
    conn.commit()

    with patch("cortex_store.routes._skill_suggest.cortex_conn", return_value=conn):
        result = run_stage_a(
            agent="claude-cursor",
            loaded=[],
            conversation_context="consult routing help",
            limit=8,
        )

    assert len(result["suggestions"]) == 1
    item = result["suggestions"][0]
    assert item["source_uri"] == "agent-skills/consult-routing.md"
    assert item["source_uri"] != "cortex://agent-skills/consult-routing.md"
    assert "uri" not in item
    assert "digest" in item


# ---------------------------------------------------------------------------
# Phase 8 — Dual-signal unreachable-skill regression tests
# ---------------------------------------------------------------------------

def _insert_full(
    conn: sqlite3.Connection,
    entity_id: str,
    *,
    source_uri: str | None,
    trigger_match_terms: list[str],
    skill_category: str | None = None,
) -> None:
    """Insert a test entity row with optional skill_category in attributes."""
    attrs: dict[str, object] = {
        "applicable_agents": ["claude-web"],
        "trigger_match_terms": trigger_match_terms,
    }
    if skill_category is not None:
        attrs["skill_category"] = skill_category
    conn.execute(
        "INSERT INTO entities (id, type, name, source_uri, lifecycle, attributes) "
        "VALUES (?, 'agent_skill', ?, ?, 'active', ?)",
        (
            entity_id,
            entity_id.removeprefix("agent_skill:"),
            source_uri,
            json.dumps(attrs),
        ),
    )


@pytest.mark.offline
def test_null_source_uri_goes_to_degraded_not_suggestions() -> None:
    """Null source_uri → degraded_skills (reason=source_uri_null); absent from suggestions.

    degraded_skills entries are NOT context-filtered: even with context that
    would never match the trigger terms, the broken skill still surfaces in
    degraded_skills (slug derivation fails before scoring is attempted).
    """
    conn = _conn()
    _insert_full(
        conn,
        "agent_skill:broken-null",
        source_uri=None,
        trigger_match_terms=["python", "refactor"],  # will NOT match context
        skill_category="session-boot-close",
    )
    conn.commit()

    with patch("cortex_store.routes._skill_suggest.cortex_conn", return_value=conn):
        result = run_stage_a(
            agent="claude-web",
            loaded=[],
            conversation_context="completely unrelated topic",
            limit=8,
        )

    assert result["suggestions"] == [], "null-URI skill must not appear in suggestions"
    assert len(result["degraded_skills"]) == 1
    entry = result["degraded_skills"][0]
    assert entry["id"] == "agent_skill:broken-null"
    assert entry["reason"] == "source_uri_null"
    assert entry["source_uri"] is None
    assert entry["skill_category"] == "session-boot-close"
    assert entry["degraded"] is True
    assert result["degraded"] is True


@pytest.mark.offline
def test_empty_source_uri_is_treated_as_null() -> None:
    """Empty-string source_uri → degraded_skills with reason=source_uri_null (falsy branch)."""
    conn = _conn()
    _insert_full(
        conn,
        "agent_skill:broken-empty",
        source_uri="",
        trigger_match_terms=["empty", "source"],
        skill_category="dispatch-delegation",
    )
    conn.commit()

    with patch("cortex_store.routes._skill_suggest.cortex_conn", return_value=conn):
        result = run_stage_a(
            agent="claude-web",
            loaded=[],
            conversation_context="empty source uri skill",
            limit=8,
        )

    assert result["suggestions"] == []
    degraded = result["degraded_skills"]
    assert len(degraded) == 1
    assert degraded[0]["reason"] == "source_uri_null"
    assert degraded[0]["skill_category"] == "dispatch-delegation"
    assert result["degraded"] is True


@pytest.mark.offline
def test_scheme_only_source_uri_goes_to_degraded_unparseable() -> None:
    """Scheme-only source_uri (cortex://) yields slug=None → degraded with reason=source_uri_unparseable."""
    conn = _conn()
    _insert_full(
        conn,
        "agent_skill:broken-scheme-only",
        source_uri="cortex://",
        trigger_match_terms=["scheme", "cortex"],
    )
    conn.commit()

    with patch("cortex_store.routes._skill_suggest.cortex_conn", return_value=conn):
        result = run_stage_a(
            agent="claude-web",
            loaded=[],
            conversation_context="scheme cortex source",
            limit=8,
        )

    assert result["suggestions"] == []
    degraded = result["degraded_skills"]
    assert len(degraded) == 1
    assert degraded[0]["reason"] == "source_uri_unparseable"
    assert degraded[0]["source_uri"] == "cortex://"
    assert degraded[0]["skill_category"] == ""  # no category in attrs → None or "" → ""
    assert result["degraded"] is True


@pytest.mark.offline
def test_parseable_uri_missing_file_stays_in_suggestions_digest_none() -> None:
    """Parseable source_uri with no file on disk → suggestions with digest=None; NOT in degraded_skills.

    This is the second unreachable-skill channel: the skill is suggestible
    (slug derivable, keywords match) but has no loadable body in the offline env.
    """
    conn = _conn()
    _insert(
        conn,
        "agent_skill:valid-uri-no-file",
        source_uri="cortex://agent-skills/does-not-exist.md",
        trigger_match_terms=["missing", "file", "valid"],
    )
    conn.commit()

    with patch("cortex_store.routes._skill_suggest.cortex_conn", return_value=conn):
        result = run_stage_a(
            agent="claude-web",
            loaded=[],
            conversation_context="missing file valid uri",
            limit=8,
        )

    assert result["degraded_skills"] == [], "parseable-URI skill must NOT appear in degraded_skills"
    assert result["degraded"] is False
    assert len(result["suggestions"]) == 1
    item = result["suggestions"][0]
    assert item["slug"] == "does-not-exist"
    assert item["source_uri"] == "cortex://agent-skills/does-not-exist.md"
    assert item["digest"] is None, "unresolvable body → digest=None in suggestions"


@pytest.mark.offline
def test_mixed_degraded_and_digest_null_channels_coexist() -> None:
    """Both unreachable channels in one response: one degraded + one digest-null suggestion.

    This is the primary regression for the dual-signal contract. Consumers must
    union both channels to triage all broken skills:
        broken = degraded_skills + [s for s in suggestions if not s["digest"]]
    """
    conn = _conn()
    # Channel 1 — null source_uri → degraded
    _insert_full(
        conn,
        "agent_skill:broken-null-mixed",
        source_uri=None,
        trigger_match_terms=["mixed", "broken"],
        skill_category="cortex-planning",
    )
    # Channel 2 — parseable URI, file missing → digest=None in suggestions
    _insert(
        conn,
        "agent_skill:valid-uri-missing-mixed",
        source_uri="cortex://agent-skills/not-a-real-file.md",
        trigger_match_terms=["mixed", "valid"],
    )
    conn.commit()

    with patch("cortex_store.routes._skill_suggest.cortex_conn", return_value=conn):
        result = run_stage_a(
            agent="claude-web",
            loaded=[],
            conversation_context="mixed broken valid uri",
            limit=8,
        )

    # Channel 1: degraded_skills
    assert len(result["degraded_skills"]) == 1
    d = result["degraded_skills"][0]
    assert d["id"] == "agent_skill:broken-null-mixed"
    assert d["reason"] == "source_uri_null"
    assert d["skill_category"] == "cortex-planning"
    assert result["degraded"] is True

    # Channel 2: suggestions with digest=None
    assert len(result["suggestions"]) == 1
    s = result["suggestions"][0]
    assert s["slug"] == "not-a-real-file"
    assert s["digest"] is None

    # Mutual exclusion: no skill appears in both channels
    degraded_ids = {e["id"] for e in result["degraded_skills"]}
    suggestion_ids = {s["id"] for s in result["suggestions"]}
    assert degraded_ids.isdisjoint(suggestion_ids), "channels must be mutually exclusive"

    # Union covers all broken skills
    broken_ids = degraded_ids | {s["id"] for s in result["suggestions"] if not s.get("digest")}
    assert broken_ids == {
        "agent_skill:broken-null-mixed",
        "agent_skill:valid-uri-missing-mixed",
    }
