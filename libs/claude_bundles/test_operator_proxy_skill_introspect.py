"""Offline tests for operator-proxy skill-introspect mid-flight honesty."""

from __future__ import annotations

import pytest

from claude_bundles.operator_proxy_skill_introspect import skill_introspection_block

pytestmark = pytest.mark.offline

_SLUGS = ("reasoning-posture", "completion-provenance-discipline")


def test_block_keeps_request_not_receipt_and_use_verb() -> None:
    out = skill_introspection_block(_SLUGS)
    assert "request**, not" in out or "request, not a receipt" in out.lower()
    assert "Use the `" in out
    assert "`<slug>` skill" in out
    for slug in _SLUGS:
        assert f"`{slug}`" in out


def test_download_failed_is_not_not_found() -> None:
    out = skill_introspection_block(_SLUGS)
    assert "download failed" in out
    assert "not** `not_found`" in out or "not `not_found`" in out
    assert "`not_yet_synced`" in out
    assert "Retry `Use the `" in out


def test_writer_die_both_predicates_named() -> None:
    out = skill_introspection_block(_SLUGS)
    assert "both true" in out
    assert "writer-die" in out
    assert "Snapshot greens" in out
    assert 'not "sync completed."' in out or 'not "sync completed."' in out


def test_mnt_skills_is_examples_mount_not_customize_mirror() -> None:
    out = skill_introspection_block(_SLUGS)
    assert "/mnt/skills/<slug>/SKILL.md" in out
    assert "examples/public mount" in out
    assert "/root/.claude/skills/synced/<slug>/SKILL.md" in out
    assert "does not prove the slug is absent from the synced mirror" in out


def test_unadvertised_absence_is_not_found() -> None:
    out = skill_introspection_block(_SLUGS)
    assert "not advertised this generation" in out
    assert "Do not chase" in out
