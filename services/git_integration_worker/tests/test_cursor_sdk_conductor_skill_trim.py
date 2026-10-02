"""Conductor HOME skill-catalog trim."""

from __future__ import annotations

from pathlib import Path

import pytest

from services.git_integration_worker.cursor_sdk_conductor_skill_trim import (
    CONDUCTOR_SKILL_KEEP,
    trim_conductor_skill_catalog,
)


def _seed(cursor: Path, slugs: list[str]) -> None:
    skills = (
        cursor
        / "plugins"
        / "local"
        / "ulg-ecosystem"
        / "skills"
    )
    for slug in slugs:
        d = skills / slug
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text(f"---\nname: {slug}\n---\n", encoding="utf-8")


def test_conductor_home_drops_unlisted_skill(tmp_path: Path) -> None:
    cursor = tmp_path / ".cursor"
    _seed(cursor, ["conductor", "not-a-conductor-skill"])
    removed = trim_conductor_skill_catalog(cursor, contract="conductor")
    assert removed == ("not-a-conductor-skill",)
    skills = cursor / "plugins" / "local" / "ulg-ecosystem" / "skills"
    assert (skills / "conductor" / "SKILL.md").is_file()
    assert not (skills / "not-a-conductor-skill").exists()


def test_implement_home_keeps_full_census(tmp_path: Path) -> None:
    cursor = tmp_path / ".cursor"
    _seed(cursor, ["conductor", "not-a-conductor-skill"])
    removed = trim_conductor_skill_catalog(cursor, contract="implement")
    assert removed == ()
    skills = cursor / "plugins" / "local" / "ulg-ecosystem" / "skills"
    assert (skills / "not-a-conductor-skill" / "SKILL.md").is_file()


def test_skills_slug_outside_keep_set_survives(tmp_path: Path) -> None:
    cursor = tmp_path / ".cursor"
    _seed(cursor, ["conductor", "admit-only-slug"])
    trim_conductor_skill_catalog(
        cursor, contract="conductor", extra_slugs=["admit-only-slug"]
    )
    skills = cursor / "plugins" / "local" / "ulg-ecosystem" / "skills"
    assert (skills / "admit-only-slug" / "SKILL.md").is_file()
    assert "admit-only-slug" not in CONDUCTOR_SKILL_KEEP


def test_empty_skills_keeps_conductor(tmp_path: Path) -> None:
    cursor = tmp_path / ".cursor"
    _seed(cursor, ["conductor", "not-a-conductor-skill"])
    trim_conductor_skill_catalog(cursor, contract="conductor", extra_slugs=[])
    skills = cursor / "plugins" / "local" / "ulg-ecosystem" / "skills"
    assert (skills / "conductor" / "SKILL.md").is_file()


def test_missing_keep_set_skill_is_logged(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    cursor = tmp_path / ".cursor"
    _seed(cursor, ["conductor"])
    with caplog.at_level("WARNING"):
        trim_conductor_skill_catalog(cursor, contract="conductor")
    assert any(
        "keep-set slugs missing from source tree:" in rec.message
        and "git-posture" in rec.message
        for rec in caplog.records
    )
    assert sum(
        "keep-set slugs missing from source tree:" in rec.message
        for rec in caplog.records
    ) == 1
    assert (cursor / "plugins" / "local" / "ulg-ecosystem" / "skills" / "conductor").is_dir()


def test_switch_off_restores_census(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CURSOR_SDK_CONDUCTOR_SKILL_TRIM", "0")
    cursor = tmp_path / ".cursor"
    _seed(cursor, ["conductor", "not-a-conductor-skill"])
    removed = trim_conductor_skill_catalog(cursor, contract="conductor")
    assert removed == ()
    assert (
        cursor / "plugins" / "local" / "ulg-ecosystem" / "skills" / "not-a-conductor-skill"
    ).is_dir()


def test_contract_guard_is_case_insensitive(tmp_path: Path) -> None:
    cursor = tmp_path / ".cursor"
    _seed(cursor, ["conductor", "not-a-conductor-skill"])
    removed = trim_conductor_skill_catalog(cursor, contract="Conductor")
    assert "not-a-conductor-skill" in removed
