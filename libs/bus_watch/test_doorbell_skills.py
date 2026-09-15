"""Tests for surface-derived doorbell skill slugs."""

from __future__ import annotations

from pathlib import Path

import pytest

from bus_watch.doorbell_skills import (
    LIAISON_PROTOCOL_ECHO,
    LIAISON_PROTOCOL_LOADER,
    dispatch_skills_for_surface,
    doorbell_skills,
    primary_liaison_slug,
)


@pytest.mark.offline
def test_doorbell_skills_preflight_ide_and_cdp() -> None:
    assert doorbell_skills("ide") == ("liaison", "reasoning-posture")
    assert doorbell_skills("cdp") == ("liaison", "reasoning-posture")
    assert primary_liaison_slug("ide") == "liaison"


@pytest.mark.offline
def test_dispatch_skills_only_on_cdp_cse_surfaces() -> None:
    assert dispatch_skills_for_surface("cdp") == ["liaison", "reasoning-posture"]
    assert dispatch_skills_for_surface("cse") == ["liaison", "reasoning-posture"]
    assert dispatch_skills_for_surface("ide") == []
    assert dispatch_skills_for_surface("cursor-sdk") == []


@pytest.mark.offline
def test_liaison_protocol_echo_marker_in_skill_body() -> None:
    repo = Path(__file__).resolve().parents[2]
    body = (repo / ".cursor/skills/liaison/SKILL.md").read_text(encoding="utf-8")
    assert LIAISON_PROTOCOL_ECHO in body
    assert LIAISON_PROTOCOL_LOADER == "stage_cdp_prompt_with_skills"


@pytest.mark.offline
def test_cdp_staging_inlines_liaison_protocol_echo(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Delivery echo proof: body-only marker sealed via cursor_only inline path."""
    from claude_bundles.catalog import clear_skill_catalog_cache, load_skill_catalog
    from claude_bundles.cdp_model_endpoint_staging import stage_cdp_prompt_with_skills

    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    clear_skill_catalog_cache()
    monkeypatch.setattr(
        "claude_bundles.catalog.get_skill_catalog",
        lambda: load_skill_catalog(validate_sot=False),
    )
    staged = stage_cdp_prompt_with_skills(
        execution_id="exec-liaison-protocol",
        prompt_text="WAKE doorbell stub\n",
        skills=dispatch_skills_for_surface("cdp"),
    )
    on_disk = (
        tmp_path / "notes/system/ephemeral/cdp-endpoint/exec-liaison-protocol/prompt.md"
    )
    text = on_disk.read_text(encoding="utf-8")
    assert LIAISON_PROTOCOL_ECHO in text
    assert '<skill slug="liaison"' in text
    assert staged.prompt_uri.endswith("exec-liaison-protocol/prompt.md")
