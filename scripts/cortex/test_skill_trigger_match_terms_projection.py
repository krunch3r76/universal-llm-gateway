"""Unit tests for trigger_match_terms ingest projection and derivation."""

from __future__ import annotations

import sys
from pathlib import Path

_SCRIPTS_CORTEX = Path(__file__).resolve().parent
_REPO = _SCRIPTS_CORTEX.parent.parent
if str(_SCRIPTS_CORTEX) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_CORTEX))
if str(_REPO / "libs") not in sys.path:
    sys.path.insert(0, str(_REPO / "libs"))

from _skill_projection import _matches, _projection  # noqa: E402
from _skill_terms import (  # noqa: E402
    canonicalize_trigger_match_terms,
    derive_projection_trigger_match_terms,
    derive_trigger_match_terms,
    derive_trigger_match_terms_from_vocab,
)


def _scanned_row(
    *,
    slug: str = "sample-skill",
    description: str = "Load when implementing dispatch workflows.",
    frontmatter: dict | None = None,
) -> dict[str, object]:
    return {
        "slug": slug,
        "frontmatter": frontmatter or {},
        "description": description,
        "source_uri": f"workspaces://universal-llm-gateway/.cursor/skills/{slug}/SKILL.md",
        "related_skills": [],
    }


def test_projection_derives_terms_when_frontmatter_omits_key() -> None:
    row = _scanned_row()
    projected = _projection(row)
    terms = projected["attributes"]["trigger_match_terms"]
    assert isinstance(terms, list)
    assert terms
    assert terms == sorted(terms, key=str.lower)
    assert "sample-skill" in terms or "sample_skill" in terms


def test_projection_preserves_explicit_frontmatter_terms_verbatim() -> None:
    explicit = ["dispatch-shape", "handoff-packet"]
    row = _scanned_row(frontmatter={"trigger_match_terms": explicit})
    projected = _projection(row)
    assert projected["attributes"]["trigger_match_terms"] == explicit


def test_matches_reports_trigger_match_terms_drift() -> None:
    row = _scanned_row(
        frontmatter={"trigger_match_terms": ["alpha", "beta"]},
    )
    live = {
        "id": "agent_skill:sample-skill",
        "type": "agent_skill",
        "lifecycle": "active",
        "description": row["description"],
        "source_uri": row["source_uri"],
        "attributes": {
            "applicable_agents": ["*"],
            "trigger_match_terms": ["alpha", "gamma"],
        },
    }
    ok, reason = _matches(live, _projection(row, live=live))
    assert ok is False
    assert "trigger_match_terms" in reason


def test_matches_canonicalized_equality_tolerates_order_case_duplicates() -> None:
    row = _scanned_row(
        frontmatter={"trigger_match_terms": ["Alpha", "beta"]},
    )
    live = {
        "id": "agent_skill:sample-skill",
        "type": "agent_skill",
        "lifecycle": "active",
        "description": row["description"],
        "source_uri": row["source_uri"],
        "attributes": {
            "applicable_agents": ["*"],
            "trigger_match_terms": ["beta", "Alpha", "ALPHA", "beta"],
        },
    }
    ok, reason = _matches(live, _projection(row, live=live))
    assert ok is True
    assert reason == ""

    live["attributes"]["trigger_match_terms"] = ["alpha", "gamma"]
    ok, reason = _matches(live, _projection(row, live=live))
    assert ok is False
    assert "trigger_match_terms" in reason


def test_matches_canonicalized_equality_tolerates_none_and_empty() -> None:
    row = _scanned_row(frontmatter={"trigger_match_terms": []})
    live = {
        "id": "agent_skill:sample-skill",
        "type": "agent_skill",
        "lifecycle": "active",
        "description": row["description"],
        "source_uri": row["source_uri"],
        "attributes": {"applicable_agents": ["*"]},
    }
    ok, reason = _matches(live, _projection(row, live=live))
    assert ok is True
    assert reason == ""


def test_backfill_canonicalizes_deterministic_and_vocab_paths(monkeypatch, tmp_path) -> None:
    import backfill_skill_trigger_match_terms as backfill

    det_path = tmp_path / ".cursor" / "skills" / "det-skill" / "SKILL.md"
    det_path.parent.mkdir(parents=True)
    det_path.write_text("---\ndescription: x\n---\n\n# Det\n", encoding="utf-8")

    entity_terms: list[list[str]] = []
    file_terms: list[list[str]] = []

    def capture_entity(_client, _entity_id, terms, *, dry_run):
        entity_terms.append(list(terms))
        return True

    def capture_file(path, terms):
        file_terms.append(list(terms))

    monkeypatch.setattr(backfill, "_patch_entity_terms", capture_entity)
    monkeypatch.setattr(backfill, "_patch_frontmatter_terms", capture_file)
    monkeypatch.setattr(backfill, "make_sync_client", lambda _url: object())
    monkeypatch.setattr(
        backfill,
        "_skill_paths",
        lambda slug, _root: (
            (None, det_path) if slug == "det-skill" else (None, None)
        ),
    )
    vocab_rows = [
        ("vocab-skill", "domain", "ZETA", 9.0, 2),
        ("vocab-skill", "domain", "alpha", 8.5, 2),
        ("vocab-skill", "domain", "zeta", 7.0, 1),
    ]

    async def fake_vocab_load(_db_path=None):
        return vocab_rows

    monkeypatch.setattr(backfill, "_load_skill_vocabulary_rows", fake_vocab_load)

    monkeypatch.setattr(
        backfill,
        "_fetch_active_skills",
        lambda _client: [
            {
                "id": "agent_skill:det-skill",
                "lifecycle": "active",
                "description": "deterministic derivation description",
                "attributes": {
                    "trigger_short": "DET det",
                    "skill_category": "test-category",
                },
            }
        ],
    )
    assert backfill.main(["--no-ingest", "--root", str(tmp_path)]) == 0

    assert len(file_terms) == 1
    det_raw = derive_trigger_match_terms(
        "det-skill",
        trigger_short="DET det",
        skill_category="test-category",
        description="deterministic derivation description",
    )
    assert file_terms[0] == canonicalize_trigger_match_terms(det_raw)

    file_terms.clear()
    entity_terms.clear()
    monkeypatch.setattr(
        backfill,
        "_fetch_active_skills",
        lambda _client: [
            {
                "id": "agent_skill:vocab-skill",
                "lifecycle": "active",
                "description": "vocab derivation description",
                "attributes": {},
            }
        ],
    )
    assert backfill.main(["--no-ingest", "--source", "vocab", "--root", str(tmp_path)]) == 0

    assert len(entity_terms) == 1
    vocab_raw = derive_trigger_match_terms_from_vocab("vocab-skill", vocab_rows=vocab_rows)
    assert entity_terms[0] == canonicalize_trigger_match_terms(vocab_raw)


def test_backfill_entity_patch_canonicalizes_existing_frontmatter_terms(
    monkeypatch, tmp_path
) -> None:
    import backfill_skill_trigger_match_terms as backfill

    skill_path = tmp_path / ".cursor" / "skills" / "existing-fm" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text(
        "---\ntrigger_match_terms: [\"Beta\", \"alpha\", \"BETA\"]\n---\n\n# Skill\n",
        encoding="utf-8",
    )

    patched: list[list[str]] = []

    def capture_entity(_client, _entity_id, terms, *, dry_run):
        patched.append(list(terms))
        return True

    monkeypatch.setattr(backfill, "_patch_entity_terms", capture_entity)
    monkeypatch.setattr(backfill, "make_sync_client", lambda _url: object())
    monkeypatch.setattr(
        backfill,
        "_fetch_active_skills",
        lambda _client: [
            {
                "id": "agent_skill:existing-fm",
                "lifecycle": "active",
                "description": "existing frontmatter skill",
                "attributes": {},
            }
        ],
    )
    monkeypatch.setattr(
        backfill,
        "_skill_paths",
        lambda _slug, _root: (None, skill_path),
    )

    assert backfill.main(["--no-ingest", "--root", str(tmp_path)]) == 0
    assert patched == [canonicalize_trigger_match_terms(["Beta", "alpha", "BETA"])]


def test_backfill_skips_non_empty_entity_terms(monkeypatch) -> None:
    import backfill_skill_trigger_match_terms as backfill

    patched: list[list[str]] = []

    def capture_entity(_client, _entity_id, terms, *, dry_run):
        patched.append(list(terms))
        return True

    monkeypatch.setattr(backfill, "_patch_entity_terms", capture_entity)
    monkeypatch.setattr(backfill, "make_sync_client", lambda _url: object())
    monkeypatch.setattr(
        backfill,
        "_fetch_active_skills",
        lambda _client: [
            {
                "id": "agent_skill:authored-terms",
                "lifecycle": "active",
                "description": "already has terms",
                "attributes": {"trigger_match_terms": ["authored-term"]},
            }
        ],
    )

    assert backfill.main(["--no-ingest"]) == 0
    assert patched == []


def test_projection_vocab_rows_take_precedence_over_description() -> None:
    row = _scanned_row(slug="vocab-skill", description="generic description text")
    vocab_rows = [
        ("vocab-skill", "domain", "routing", 9.5, 3),
        ("vocab-skill", "domain", "dispatch", 8.0, 2),
        ("other-skill", "domain", "ignored", 10.0, 1),
    ]
    projected = _projection(row, vocab_rows=vocab_rows)
    terms = projected["attributes"]["trigger_match_terms"]
    assert terms == ["dispatch", "routing"]


def test_projection_description_only_when_vocab_rows_none() -> None:
    row = _scanned_row(
        slug="desc-only",
        description="Use when closing sessions with provenance discipline.",
        frontmatter={"skill_category": "session-close"},
    )
    projected = _projection(row, vocab_rows=None)
    terms = projected["attributes"]["trigger_match_terms"]
    assert terms
    expected = derive_projection_trigger_match_terms(
        "desc-only",
        frontmatter=row["frontmatter"],
        description=str(row["description"]),
        vocab_rows=None,
    )
    assert terms == expected


def test_projection_sources_trigger_short_and_category_from_frontmatter() -> None:
    row = _scanned_row(
        slug="fm-sourced",
        description="Close sessions with structured handoff.",
        frontmatter={
            "trigger_short": "operator closes session",
            "skill_category": "session-close",
        },
    )
    projected = _projection(row)
    terms = projected["attributes"]["trigger_match_terms"]
    expected = derive_projection_trigger_match_terms(
        "fm-sourced",
        frontmatter=row["frontmatter"],
        description=str(row["description"]),
        vocab_rows=None,
    )
    assert terms == expected
    assert "session" in terms or "close" in terms
