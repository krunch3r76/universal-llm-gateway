"""Tests for surface-derived doorbell skill slugs."""

from __future__ import annotations

from pathlib import Path

import pytest

from bus_watch.doorbell_skills import (
    LIAISON_PROTOCOL_ECHO,
    LIAISON_PROTOCOL_LOADER,
    dispatch_skills_for_surface,
    doorbell_skills,
    is_dispatch_skills_surface,
    navigator_doorbell_skills_from_policy,
    primary_liaison_slug,
    seat_dispatch_surface,
    seat_doorbell_surface,
)


@pytest.mark.offline
def test_doorbell_skills_preflight_ide_and_cdp() -> None:
    assert doorbell_skills("ide") == ("ide-continuity", "reasoning-posture")
    assert doorbell_skills("cdp") == ("reasoning-posture",)
    assert doorbell_skills("life") == ("reasoning-posture",)
    assert primary_liaison_slug("ide") == "ide-continuity"
    assert primary_liaison_slug("life") == "ide-continuity"


@pytest.mark.offline
def test_preflight_drops_cursor_only_on_non_cursor_surfaces() -> None:
    """AC3 — ``cursor_only`` slugs are shed from Use-lines on life/CDP surfaces."""
    assert doorbell_skills("ide") == ("ide-continuity", "reasoning-posture")
    assert doorbell_skills("life") == ("reasoning-posture",)
    assert doorbell_skills("cdp") == ("reasoning-posture",)


@pytest.mark.offline
def test_seat_dispatch_surface_maps_life_seats() -> None:
    assert seat_dispatch_surface("web-anthropic") == "life"
    assert seat_doorbell_surface("web-anthropic") == "life"
    assert is_dispatch_skills_surface("web-anthropic")


@pytest.mark.offline
def test_navigator_doorbell_skills_from_policy_override() -> None:
    policy = {
        "navigator_skills": [
            "ide-continuity",
            "reasoning-posture",
            "architecture-invariants",
            "ulg-architecture",
        ],
    }
    assert navigator_doorbell_skills_from_policy(policy) == (
        "ide-continuity",
        "reasoning-posture",
        "architecture-invariants",
        "ulg-architecture",
    )
    assert navigator_doorbell_skills_from_policy({}) == doorbell_skills("cursor-sdk")


@pytest.mark.offline
def test_seat_dispatch_surface_maps_cdp_cse_seats() -> None:
    assert seat_dispatch_surface("cdp") == "cdp"
    assert seat_dispatch_surface("cdp/opus-5") == "cdp"
    assert seat_dispatch_surface("cse") == "cse"
    assert seat_dispatch_surface("cursor-sdk") is None
    assert is_dispatch_skills_surface("cdp")


@pytest.mark.offline
def test_dispatch_skills_only_on_cdp_cse_life_surfaces() -> None:
    assert dispatch_skills_for_surface("cdp") == ["ide-continuity", "reasoning-posture"]
    assert dispatch_skills_for_surface("cse") == ["ide-continuity", "reasoning-posture"]
    assert dispatch_skills_for_surface("life") == ["ide-continuity", "reasoning-posture"]
    assert dispatch_skills_for_surface("ide") == []
    assert dispatch_skills_for_surface("cursor-sdk") == []


# friction:39814 — a third retry of the same input is still forbidden; one
# resolve-or-replace attempt must be visible in ide-continuity skill text.
REPEATED_FAILURE_RULE = (
    "Same input failed twice ⇒ no more retries of that input; one attempt of a "
    "different kind (replace the input) is allowed before REPEATED_FAILURE; a "
    "third retry of the same input is forbidden."
)
FOL_REPEATED_FAILURE = (
    "same_input_failed_twice ⇒ ¬retry(same_input); "
    "one attempt(different_kind: replace_input) before REPEATED_FAILURE"
)


@pytest.mark.offline
def test_ide_continuity_repeated_failure_resolve_or_replace_in_skill_body() -> None:
    repo = Path(__file__).resolve().parents[2]
    skill_path = repo / "cursor-plugins/ulg-ecosystem/skills/ide-continuity/SKILL.md"
    kernel_path = (
        repo / "cursor-plugins/ulg-ecosystem/rules/dispatch-kernel_ulg.mdc"
    )
    friction_rows_path = repo / "libs/bus_watch/friction_rows.py"
    body = skill_path.read_text(encoding="utf-8")
    kernel = kernel_path.read_text(encoding="utf-8")
    friction_rows = friction_rows_path.read_text(encoding="utf-8")
    assert body.count(REPEATED_FAILURE_RULE) == 3
    assert REPEATED_FAILURE_RULE in kernel
    assert REPEATED_FAILURE_RULE in friction_rows
    assert FOL_REPEATED_FAILURE in body
    assert "resolve or replace the input" not in body
    repeated_failure_lines = [
        line
        for line in body.splitlines()
        if "REPEATED_FAILURE" in line or REPEATED_FAILURE_RULE in line
    ]
    assert repeated_failure_lines
    assert not any("resolve" in line for line in repeated_failure_lines)


@pytest.mark.offline
def test_liaison_protocol_echo_marker_in_skill_body() -> None:
    repo = Path(__file__).resolve().parents[2]
    body = (
        repo / "cursor-plugins/ulg-ecosystem/skills/ide-continuity/SKILL.md"
    ).read_text(encoding="utf-8")
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
    relaxed = lambda: load_skill_catalog(validate_sot=False)
    monkeypatch.setattr("claude_bundles.catalog.get_skill_catalog", relaxed)
    monkeypatch.setattr(
        "claude_bundles.cowork_skill_delivery.get_skill_catalog",
        relaxed,
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
    assert '<skill slug="ide-continuity"' in text
    assert staged.prompt_uri.endswith("exec-liaison-protocol/prompt.md")
