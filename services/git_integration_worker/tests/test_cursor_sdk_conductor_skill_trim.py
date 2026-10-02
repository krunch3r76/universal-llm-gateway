"""Conductor HOME skill-catalog trim."""

from __future__ import annotations

from pathlib import Path

import pytest

from services.git_integration_worker.cursor_sdk_conductor_skill_trim import (
    CONDUCTOR_SKILL_KEEP,
    trim_conductor_skill_catalog,
)

_ROUTE = (
    Path(__file__).resolve().parents[1] / "routes" / "cursor_sdk.py"
)
_PARK = Path(__file__).resolve().parents[1] / "cursor_sdk_park_resume.py"
_HOP = (
    Path(__file__).resolve().parents[1]
    / "cursor_sdk_closeout"
    / "conductor_hop.py"
)
_WATCH = (
    Path(__file__).resolve().parents[1]
    / "cursor_sdk_closeout"
    / "conductor_hop_watchdog.py"
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
        "keep-set slug git-posture missing from source tree" in rec.message
        for rec in caplog.records
    )
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


def test_shared_trim_is_reached_by_each_home_build_path() -> None:
    route = _ROUTE.read_text(encoding="utf-8")
    assert "trim_conductor_skill_catalog(" in route
    assert route.count("_run_sdk_dispatch_gated(") >= 2
    park = _PARK.read_text(encoding="utf-8")
    assert "admit_cursor_dispatch" in park
    hop = _HOP.read_text(encoding="utf-8")
    assert "def post_conductor_hop_team_dispatch" in hop
    watch = _WATCH.read_text(encoding="utf-8")
    assert "post_conductor_hop_team_dispatch" in watch
    # generate + queued promote both enter the gated runner, which calls sync.
    assert "asyncio.to_thread(\n            _run_sdk_sync," in route
