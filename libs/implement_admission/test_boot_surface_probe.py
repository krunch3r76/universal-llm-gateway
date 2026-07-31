"""Tests for rendered boot-surface skill pointer probe (AC21)."""

from __future__ import annotations

import pytest

from implement_admission.boot_surface_probe import (
    probe_boot_manifest,
    probe_rendered_surface,
)


@pytest.mark.offline
def test_probe_resolves_known_entity_token() -> None:
    text = "Load `agent_skill:architecture-invariants` on demand."
    report = probe_rendered_surface(text, platform="web")
    assert report.ok
    assert report.pointers_checked >= 1


@pytest.mark.offline
def test_probe_flags_unknown_slug() -> None:
    text = "See agent_skill:totally-absent-skill-xyz for details."
    report = probe_rendered_surface(text)
    assert not report.ok
    assert any(v.reason == "unresolved_slug" for v in report.violations)


@pytest.mark.offline
def test_probe_flags_web_boot_lead_doc_only() -> None:
    text = "Follow web-boot-lead guidance and agent_skill:web-boot-lead."
    report = probe_rendered_surface(text)
    assert not report.ok
    assert any(v.slug == "web-boot-lead" for v in report.violations)


@pytest.mark.offline
def test_probe_boot_manifest_card_markdown() -> None:
    manifest = {
        "briefing_card": (
            "## Skills\n- `git-posture` — "
            "fs(sandbox=\"workspaces\", path=\".cursor/skills/git-posture/SKILL.md\")"
        ),
    }
    report = probe_boot_manifest(manifest, platform="web")
    assert report.ok
