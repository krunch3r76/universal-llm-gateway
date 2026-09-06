"""Tests for staging ``skills=`` into the dispatch HOME user layer.

Acceptance target: a cursor-sdk dispatch naming ``skills=["prose-discipline"]``
ends up with that body on a path ``setting_sources=("all",)`` discovers, without
the worker or the seat fs-reading a SKILL.md path.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from services.git_integration_worker.cursor_sdk_packet import resolve_prompt_preamble
from services.git_integration_worker.cursor_sdk_skills_mount import (
    HOME_SKILLS_DIRNAME,
    discoverable_skill_dirs,
    stage_dispatch_skills,
)

_PLUGIN_SLUG = "reasoning-posture"
_CURSOR_SLUG = "ulg-architecture"
_LIFE_SLUG = "prose-discipline"
_LIFE_SLUG_2 = "outbound-voice-spec"


@pytest.fixture
def hub(tmp_path: Path) -> Path:
    """A stand-in hub checkout carrying one body per SoT layer."""
    root = tmp_path / "hub"
    for relpath, slug in (
        ("cursor-plugins/ulg-ecosystem/skills", _PLUGIN_SLUG),
        (".cursor/skills", _CURSOR_SLUG),
        (".claude/skills", _LIFE_SLUG),
        (".claude/skills", _LIFE_SLUG_2),
    ):
        path = root / relpath / slug / "SKILL.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"---\nname: {slug}\n---\n# {slug}\n", encoding="utf-8")
    return root


@pytest.fixture
def cursor_dir(tmp_path: Path) -> Path:
    """A dispatch HOME ``.cursor`` seeded the way ``setup_cursor_dispatch_home`` leaves it."""
    path = tmp_path / "dispatch-home" / ".cursor"
    census = path / "plugins" / "local" / "ulg-ecosystem" / "skills" / _PLUGIN_SLUG
    census.mkdir(parents=True)
    (census / "SKILL.md").write_text("---\nname: reasoning-posture\n---\n", "utf-8")
    return path


@pytest.mark.offline
def test_life_local_body_is_staged_into_home_user_layer(
    cursor_dir: Path, hub: Path
) -> None:
    result = stage_dispatch_skills(cursor_dir, [_LIFE_SLUG], source_repo=hub)

    dest = cursor_dir / HOME_SKILLS_DIRNAME / _LIFE_SLUG / "SKILL.md"
    assert dest.is_file()
    assert f"name: {_LIFE_SLUG}" in dest.read_text(encoding="utf-8")
    assert result.staged_slugs == (_LIFE_SLUG,)
    assert result.mounted_slugs == (_LIFE_SLUG,)
    assert result.unresolved_slugs == ()
    assert result.rows[0].layer == "life_local"
    assert result.rows[0].dest == str(dest)


@pytest.mark.offline
def test_plugin_census_slug_is_not_restaged(cursor_dir: Path, hub: Path) -> None:
    """Two SKILL.md files sharing one ``name:`` is a duplicate-slug hazard."""
    result = stage_dispatch_skills(cursor_dir, [_PLUGIN_SLUG], source_repo=hub)

    assert result.preexisting_slugs == (_PLUGIN_SLUG,)
    assert result.staged_slugs == ()
    assert not (cursor_dir / HOME_SKILLS_DIRNAME / _PLUGIN_SLUG).exists()
    assert result.mounted_slugs == (_PLUGIN_SLUG,)


@pytest.mark.offline
def test_workspace_skill_is_not_restaged(cursor_dir: Path, hub: Path) -> None:
    """A slug the seat already reads from ``{cwd}/.cursor/skills`` needs no copy."""
    result = stage_dispatch_skills(
        cursor_dir,
        [_CURSOR_SLUG],
        source_repo=hub,
        workspace_roots=(hub,),
    )

    assert result.preexisting_slugs == (_CURSOR_SLUG,)
    assert not (cursor_dir / HOME_SKILLS_DIRNAME / _CURSOR_SLUG).exists()


@pytest.mark.offline
def test_workspace_slug_is_staged_when_no_workspace_root_given(
    cursor_dir: Path, hub: Path
) -> None:
    result = stage_dispatch_skills(cursor_dir, [_CURSOR_SLUG], source_repo=hub)

    assert result.staged_slugs == (_CURSOR_SLUG,)
    assert (cursor_dir / HOME_SKILLS_DIRNAME / _CURSOR_SLUG / "SKILL.md").is_file()


@pytest.mark.offline
def test_mixed_request_partitions_by_disposition(cursor_dir: Path, hub: Path) -> None:
    result = stage_dispatch_skills(
        cursor_dir,
        [_LIFE_SLUG, _LIFE_SLUG_2, _PLUGIN_SLUG, "definitely-not-a-skill"],
        source_repo=hub,
    )

    assert result.staged_slugs == (_LIFE_SLUG, _LIFE_SLUG_2)
    assert result.preexisting_slugs == (_PLUGIN_SLUG,)
    assert result.unresolved_slugs == ("definitely-not-a-skill",)
    assert len(result.rows) == 4


@pytest.mark.offline
def test_unresolvable_slug_is_recorded_not_raised(cursor_dir: Path, hub: Path) -> None:
    """Stargate fail-closes at admit; a miss here means the body moved since."""
    result = stage_dispatch_skills(
        cursor_dir, ["definitely-not-a-skill"], source_repo=hub
    )

    assert result.unresolved_slugs == ("definitely-not-a-skill",)
    assert result.mounted_slugs == ()
    assert "absent from skill catalog" in (result.rows[0].reason or "")


@pytest.mark.offline
def test_hub_anchor_is_required_for_life_local(cursor_dir: Path, tmp_path: Path) -> None:
    """Resolving against a worktree root (no ``.claude``) must not silently mount."""
    worktree = tmp_path / "lane-b"
    worktree.mkdir()

    result = stage_dispatch_skills(cursor_dir, [_LIFE_SLUG], source_repo=worktree)

    assert result.staged_slugs == ()
    assert result.unresolved_slugs == (_LIFE_SLUG,)


@pytest.mark.offline
def test_empty_skills_is_noop(cursor_dir: Path, hub: Path) -> None:
    for empty in (None, [], ["", "  "]):
        result = stage_dispatch_skills(cursor_dir, empty, source_repo=hub)
        assert result.rows == ()
        assert not (cursor_dir / HOME_SKILLS_DIRNAME).exists()


@pytest.mark.offline
def test_staging_is_idempotent(cursor_dir: Path, hub: Path) -> None:
    first = stage_dispatch_skills(cursor_dir, [_LIFE_SLUG], source_repo=hub)
    second = stage_dispatch_skills(cursor_dir, [_LIFE_SLUG], source_repo=hub)

    assert first.staged_slugs == (_LIFE_SLUG,)
    # Second pass sees its own copy in the HOME user layer only if that dir is
    # named as discoverable; it is not, so a re-stage overwrites in place.
    assert second.staged_slugs == (_LIFE_SLUG,)
    dest = cursor_dir / HOME_SKILLS_DIRNAME / _LIFE_SLUG / "SKILL.md"
    assert dest.read_text(encoding="utf-8").count("name:") == 1


@pytest.mark.offline
def test_discoverable_dirs_cover_home_census_and_each_workspace(
    cursor_dir: Path, tmp_path: Path
) -> None:
    dirs = discoverable_skill_dirs(
        cursor_dir, workspace_roots=(tmp_path / "a", tmp_path / "b")
    )

    assert dirs[0] == cursor_dir / "plugins/local/ulg-ecosystem/skills"
    assert dirs[1] == tmp_path / "a" / ".cursor" / "skills"
    assert dirs[2] == tmp_path / "b" / ".cursor" / "skills"


@pytest.mark.offline
def test_event_payload_carries_one_row_per_requested_slug(
    cursor_dir: Path, hub: Path
) -> None:
    result = stage_dispatch_skills(
        cursor_dir, [_LIFE_SLUG, _PLUGIN_SLUG, "nope"], source_repo=hub
    )

    payload = result.as_event_payload()
    assert {row["canonical_slug"] for row in payload} == {
        _LIFE_SLUG,
        _PLUGIN_SLUG,
        "nope",
    }
    assert {row["disposition"] for row in payload} == {
        "staged",
        "preexisting",
        "unresolved",
    }


# --- the invoke half: staging makes a body discoverable, the Use-line activates it


@pytest.mark.offline
def test_requested_skills_get_invoke_lines() -> None:
    preamble = resolve_prompt_preamble(
        handoff_contract="light-bounded",
        prompt_preamble=None,
        inferred_contract=None,
        skills=[_LIFE_SLUG, _LIFE_SLUG_2],
    )

    assert "REQUESTED SKILLS (mandatory)" in preamble
    assert f"Use the `{_LIFE_SLUG}` skill." in preamble
    assert f"Use the `{_LIFE_SLUG_2}` skill." in preamble


@pytest.mark.offline
def test_fixed_preamble_slug_is_not_invoked_twice() -> None:
    preamble = resolve_prompt_preamble(
        handoff_contract="light-bounded",
        prompt_preamble=None,
        inferred_contract=None,
        skills=["reasoning-posture", "ulg-for-llms", _LIFE_SLUG],
    )

    assert preamble.count("Use the `reasoning-posture` skill") == 1
    assert preamble.count("Use the `ulg-for-llms` skill") == 1
    assert preamble.count(f"Use the `{_LIFE_SLUG}` skill") == 1


@pytest.mark.offline
def test_packet_carried_invoke_suppresses_the_generated_one() -> None:
    preamble = resolve_prompt_preamble(
        handoff_contract="light-bounded",
        prompt_preamble=None,
        inferred_contract=None,
        existing_text=f"Use the `{_LIFE_SLUG}` skill for the draft.",
        skills=[_LIFE_SLUG, _LIFE_SLUG_2],
    )

    assert f"Use the `{_LIFE_SLUG}` skill." not in preamble
    assert f"Use the `{_LIFE_SLUG_2}` skill." in preamble


@pytest.mark.offline
def test_entity_id_form_invokes_bare_slug() -> None:
    preamble = resolve_prompt_preamble(
        handoff_contract="implement",
        prompt_preamble=None,
        inferred_contract=None,
        skills=[f"agent_skill:{_LIFE_SLUG}"],
    )

    assert f"Use the `{_LIFE_SLUG}` skill." in preamble
    assert "agent_skill:" not in preamble


@pytest.mark.offline
def test_no_skills_emits_no_requested_block() -> None:
    preamble = resolve_prompt_preamble(
        handoff_contract="implement",
        prompt_preamble=None,
        inferred_contract=None,
    )

    assert "REQUESTED SKILLS" not in preamble
