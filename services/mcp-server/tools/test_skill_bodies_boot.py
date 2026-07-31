"""Tests for boot-time agent skill index (manifest-only, bodies on demand)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from tools._boot_helpers._manifest import build_manifest
from tools._boot_helpers._skill_bodies import skill_relpath, skill_slug

_REPO = Path(__file__).resolve().parents[3]
_BACKFILL = _REPO / "scripts" / "cortex" / "backfill_skill_trigger_match_terms.py"


def _render_card(skills: list[dict]) -> str:
    sys.path.insert(0, str(_REPO / "libs"))
    from cortex_store.skill_listing_format import render_skills_card_section

    return render_skills_card_section(skills)


def test_manifest_skills_hint_catalog_self_fetch_contract() -> None:
    """Skills manifest hint directs self-fetch by slug; fs path refs retired."""
    skills = [{"name": "sample-skill", "skill_category": "protocol"}]
    manifest = build_manifest(
        plan_phases=None,
        in_flight_todos=None,
        todo_total=0,
        unread_count=0,
        reflective_total=0,
        recent_mentions=None,
        skills=skills,
    )
    skills_row = next(row for row in manifest if row.get("section") == "skills")
    hint = skills_row["hint"]
    assert "Index on briefing_card ## Agent Skills" in hint
    assert "Use the `<slug>` skill" in hint
    assert "seat self-fetches" in hint
    assert "agent-skills/` retired" in hint
    assert "GET /skills" in hint


def test_skill_slug_prefers_entity_id_over_display_name() -> None:
    """Slug derives from the entity id (on-disk form), never the display name."""
    row = {
        "id": "agent_skill:architecture-invariants",
        "name": "Architecture Invariants — Universal Layer",
    }
    assert skill_slug(row) == "architecture-invariants"
    assert skill_relpath(row) == (
        ".cursor/skills/architecture-invariants/SKILL.md"
    )


def test_skill_slug_accepts_entity_id_key() -> None:
    row = {
        "entity_id": "agent_skill:ulg-architecture",
        "name": "ULG Architecture — Layer",
    }
    assert skill_slug(row) == "ulg-architecture"
    assert skill_relpath(row) == ".cursor/skills/ulg-architecture/SKILL.md"


def test_skill_relpath_resolves_plugin_sot_from_catalog() -> None:
    row = {"id": "agent_skill:path-sim", "name": "Path Sim"}
    rel = skill_relpath(row)
    assert rel.startswith("cursor-plugins/ulg-ecosystem/skills/path-sim/SKILL.md")


def test_skill_slug_falls_back_to_name_raises_when_absent_from_catalog() -> None:
    from implement_admission.skill_catalog_resolver import SkillCatalogResolveError

    with pytest.raises(SkillCatalogResolveError):
        skill_relpath({"name": "custom-skill-absent-from-source-table"})


def test_render_skills_index_not_full_body() -> None:
    skills = [
        {
            "name": "sample-skill",
            "skill_category": "protocol",
            "trigger_short": "Do the thing when asked",
        }
    ]
    rendered = _render_card(skills)
    assert "## Agent Skills (1 active — concise manifest)" in rendered
    assert "Use the <slug> skill" in rendered
    assert "seat self-fetches" in rendered
    assert "agent-skills/" in rendered
    assert "- `sample-skill` — Do the thing when asked" in rendered
    assert 'op="md_read", path="agent-skills/sample-skill.md"' not in rendered
    assert "# Sample Skill" not in rendered
    assert "**protocol (1)**" in rendered


def test_trigger_short_preferred_over_description() -> None:
    skills = [
        {
            "name": "sample-skill",
            "skill_category": "misc",
            "trigger_short": "Short trigger",
            "description_first_sentence": "Longer fallback sentence",
        }
    ]
    rendered = _render_card(skills)
    assert "- `sample-skill` — Short trigger" in rendered
    assert "Longer fallback sentence" not in rendered


def test_render_skills_without_trigger() -> None:
    skills = [{"name": "bare-skill", "skill_category": "discipline"}]
    rendered = _render_card(skills)
    assert "- `bare-skill`" in rendered
    assert " — " not in rendered.split("- `bare-skill`")[1].split("\n")[0]


def test_flat_list_includes_all_skills() -> None:
    skills = [
        {"name": f"skill-{i}", "skill_category": "misc", "trigger_short": f"t{i}"}
        for i in range(5)
    ]
    rendered = _render_card(skills)
    for i in range(5):
        assert f"- `skill-{i}`" in rendered


def test_no_tier_headers() -> None:
    skills = [
        {"name": "dispatch-shape", "boot_importance": "required_gate"},
        {"name": "other-skill", "skill_category": "misc", "trigger_short": "misc"},
    ]
    rendered = _render_card(skills)
    assert "### Required gates" not in rendered
    assert "### Relevant now" not in rendered
    assert "### Catalog" not in rendered


def test_gate_marker_on_gates_only() -> None:
    skills = [
        {
            "name": "dispatch-shape",
            "boot_importance": "required_gate",
            "skill_category": "dispatch",
            "trigger_short": "dispatch JSON",
        },
        {
            "name": "other-skill",
            "skill_category": "misc",
            "trigger_short": "misc skill",
        },
    ]
    rendered = _render_card(skills)
    assert "> `⚑` = required gate." in rendered
    assert "- ⚑ `dispatch-shape`" in rendered
    assert "- `other-skill`" in rendered
    assert "⚑ `other-skill`" not in rendered


def test_tags_net_new_only() -> None:
    skills = [
        {
            "name": "tagged-skill",
            "skill_category": "misc",
            "trigger_short": "plan deck",
            "trigger_match_terms": ["plan", "deck", "implementation"],
        }
    ]
    rendered = _render_card(skills)
    assert "- `tagged-skill` — plan deck [implementation]" in rendered
    assert "[plan" not in rendered
    assert "[deck" not in rendered


def test_tags_all_overlap_renders_no_suffix() -> None:
    skills = [
        {
            "name": "overlap-skill",
            "skill_category": "misc",
            "trigger_short": "plan deck",
            "trigger_match_terms": ["plan", "deck"],
        }
    ]
    rendered = _render_card(skills)
    assert "- `overlap-skill` — plan deck" in rendered
    assert "[" not in rendered.split("- `overlap-skill`")[1].split("\n")[0]


def test_tags_capped() -> None:
    skills = [
        {
            "name": "many-tags",
            "skill_category": "misc",
            "trigger_short": "base",
            "trigger_match_terms": ["a", "b", "c", "d", "e"],
        }
    ]
    rendered = _render_card(skills)
    line = [ln for ln in rendered.splitlines() if ln.startswith("- `many-tags`")][0]
    assert line.count(",") <= 2


def test_skill_discovery_directive_present() -> None:
    skills = [{"name": "sample-skill", "skill_category": "protocol"}]
    rendered = _render_card(skills)
    assert "Discovery (native)" in rendered
    assert "<available_skills>" in rendered
    assert ".cursor/skills" in rendered


def test_web_boot_uses_full_active_skills_catalog() -> None:
    skills = [
        {
            "id": "agent_skill:dispatch-shape",
            "boot_importance": "required_gate",
            "skill_category": "dispatch",
            "trigger_short": "JSON arguments string",
        },
        {
            "id": "agent_skill:other",
            "skill_category": "misc",
            "trigger_short": "misc skill",
        },
    ]
    rendered = _render_card(skills)
    assert "active — concise manifest" in rendered
    assert "**dispatch (1)**" in rendered
    assert "- ⚑ `dispatch-shape`" in rendered
    assert "- `other`" in rendered


def test_backfill_derive_excludes_procedural_stopwords() -> None:
    sys.path.insert(0, str(_REPO / "scripts" / "cortex"))
    from backfill_skill_trigger_match_terms import derive_trigger_match_terms

    terms = derive_trigger_match_terms(
        "implementation-plan-workflow",
        trigger_short="when any task plan",
        skill_category="dispatch-delegation",
        description=(
            "On any task to author a multi-phase implementation plan deck "
            "from a non-Cursor seat"
        ),
    )
    lowered = {t.lower() for t in terms}
    assert "when" not in lowered
    assert "any" not in lowered
    assert "task" not in lowered
    assert "implementation-plan-workflow" in terms
    assert "implementation" in lowered
    assert "plan" in lowered


def test_backfill_audit_exits_nonzero_when_empty(monkeypatch) -> None:
    sys.path.insert(0, str(_REPO / "scripts" / "cortex"))
    import backfill_skill_trigger_match_terms as backfill_mod

    class _Client:
        def request(self, method: str, path: str, **kwargs: object) -> object:
            class _Resp:
                status_code = 200

                def json(self) -> dict:
                    if path.startswith("/entities/agent_skill:"):
                        return {
                            "id": "agent_skill:empty-skill",
                            "lifecycle": "active",
                            "attributes": {},
                        }
                    return {"items": [{"id": "agent_skill:empty-skill"}]}

            return _Resp()

    monkeypatch.setattr(backfill_mod, "make_sync_client", lambda _url: _Client())
    assert backfill_mod.audit(_Client()) == 1


def test_backfill_audit_exits_zero_when_none_empty(monkeypatch) -> None:
    sys.path.insert(0, str(_REPO / "scripts" / "cortex"))
    import backfill_skill_trigger_match_terms as backfill_mod

    class _Client:
        def request(self, method: str, path: str, **kwargs: object) -> object:
            class _Resp:
                status_code = 200

                def json(self) -> dict:
                    if path.startswith("/entities/agent_skill:"):
                        return {
                            "id": "agent_skill:full-skill",
                            "lifecycle": "active",
                            "attributes": {
                                "trigger_match_terms": ["plan", "deck"]
                            },
                        }
                    return {"items": [{"id": "agent_skill:full-skill"}]}

            return _Resp()

    monkeypatch.setattr(backfill_mod, "make_sync_client", lambda _url: _Client())
    assert backfill_mod.audit(_Client()) == 0


@pytest.mark.integration
def test_backfill_script_importable() -> None:
    proc = subprocess.run(
        [sys.executable, str(_BACKFILL), "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0
    assert "--audit" in proc.stdout


def test_f2b_card_parity_skills_view_boot_render() -> None:
    """Boot card skills block matches golden via GET /skills?view=boot."""
    from unittest.mock import patch

    sys.path.insert(0, str(_REPO / "libs"))
    from cortex_store.routes.skills import get_skills
    from cortex_store.test_skills_view_boot_parity import _parity_fixture_conn
    from cortex_store.tests.boot_card_golden import assert_card_matches_legacy_golden

    view_conn = _parity_fixture_conn()
    with patch("cortex_store.routes.skills.cortex_conn", return_value=view_conn):
        view = get_skills(
            limit=120,
            layer="skills",
            for_agent="claude-web",
            view="boot",
            render="card",
        )

    assert_card_matches_legacy_golden(view["rendered"]["card_markdown"])


def test_f2b_concise_sidecar_from_skills_view_boot() -> None:
    """Server concise markdown comes from GET /skills?view=boot&render=concise."""
    import json
    import sqlite3
    from unittest.mock import patch

    sys.path.insert(0, str(_REPO / "libs"))
    from cortex_store.routes.skills import get_skills
    from cortex_store.skill_listing_format import render_concise_skill_index

    schema = """
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
        conn.executescript(schema)
        return conn

    def _insert(
        conn: sqlite3.Connection,
        entity_id: str,
        *,
        applicable_agents: list[str] | None = None,
        attributes_extra: dict[str, object] | None = None,
    ) -> None:
        attrs: dict[str, object] = {}
        if applicable_agents is not None:
            attrs["applicable_agents"] = applicable_agents
        if attributes_extra:
            attrs.update(attributes_extra)
        conn.execute(
            "INSERT INTO entities (id, type, name, description, source_uri, lifecycle, attributes) "
            "VALUES (?, 'agent_skill', ?, 'Trigger text.', NULL, 'active', ?)",
            (
                entity_id,
                entity_id.removeprefix("agent_skill:"),
                json.dumps(attrs) if attrs else None,
            ),
        )

    view_conn = _conn()
    _insert(
        view_conn,
        "agent_skill:alpha",
        applicable_agents=["claude-web"],
        attributes_extra={
            "trigger_short": "alpha trigger",
            "skill_category": "planning",
            "description_first_sentence": "Alpha does alpha",
        },
    )
    view_conn.commit()

    with patch("cortex_store.routes.skills.cortex_conn", return_value=view_conn):
        view = get_skills(
            limit=120,
            layer="skills",
            for_agent="claude-web",
            view="boot",
            render="concise",
        )

    expected = render_concise_skill_index(view["items"])
    assert view["rendered"]["concise_markdown"] == expected
