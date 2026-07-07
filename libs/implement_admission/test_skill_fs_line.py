"""Tests for skill source_uri → fs line resolution (D1 / dispatch-skill-uri-alignment)."""

from __future__ import annotations

from pathlib import Path

import pytest
from agent_seat.guidance_entity import entity_slug_from_id
from agent_seat.inject_registry import (
    CODING_SESSION_ADVERTISE_SLUGS,
    coding_scope_inject_entity_ids,
)

from implement_admission.materialize import materialize
from implement_admission.skill_fs_line import (
    resolve_skill_source_uri,
    skill_slug_to_fs_line,
    source_uri_to_fs_line,
)
from implement_admission.skill_source_table import (
    CANONICAL_SKILL_SOURCE_URIS,
    SkillSourceResolveError,
    canonical_table_key,
)
from implement_admission.test_materialize import _sample_spec


@pytest.mark.offline
def test_source_uri_to_fs_line_workspaces() -> None:
    line = source_uri_to_fs_line(
        "workspaces://universal-llm-gateway/.cursor/skills/git-posture/SKILL.md"
    )
    assert line == (
        'fs(sandbox="workspaces", op="md_read", '
        'path="universal-llm-gateway/.cursor/skills/git-posture/SKILL.md")'
    )


@pytest.mark.offline
def test_source_uri_to_fs_line_cortex_scheme() -> None:
    line = source_uri_to_fs_line("cortex://agent-skills/implement-work-item.md")
    assert line == (
        'fs(sandbox="cortex", op="md_read", path="agent-skills/implement-work-item.md")'
    )


@pytest.mark.offline
def test_source_uri_to_fs_line_cursor_skill_path() -> None:
    line = source_uri_to_fs_line(
        "workspaces://universal-llm-gateway/.cursor/skills/implement-work-item/SKILL.md"
    )
    assert 'fs(sandbox="workspaces"' in line
    assert "implement-work-item/SKILL.md" in line


@pytest.mark.offline
def test_skill_slug_to_fs_line_known_consolidated_slug() -> None:
    line = skill_slug_to_fs_line("git-posture")
    assert 'fs(sandbox="workspaces"' in line
    assert "git-posture/SKILL.md" in line


@pytest.mark.offline
def test_skill_slug_to_fs_line_unknown_slug_raises() -> None:
    with pytest.raises(SkillSourceResolveError):
        skill_slug_to_fs_line("custom-skill-absent-from-table")


@pytest.mark.offline
def test_resolve_skill_source_uri_rule_alias() -> None:
    uri = resolve_skill_source_uri("rule:architecture-invariants")
    assert uri == CANONICAL_SKILL_SOURCE_URIS["architecture-invariants"]


@pytest.mark.offline
def test_canonical_table_key_ulg_suffix_no_longer_aliases() -> None:
    assert canonical_table_key("ulg-architecture") == "ulg-architecture"
    assert canonical_table_key("ulg-architecture_ulg") == "ulg-architecture_ulg"
    assert canonical_table_key("rule:ulg-architecture_ulg") == "ulg-architecture_ulg"


@pytest.mark.offline
def test_canonical_table_key_session_close_kernel_alias() -> None:
    assert canonical_table_key("session-close-kernel") == "session-close"


@pytest.mark.offline
def test_doc_type_hot_path_slugs_resolve() -> None:
    """Regression: doc_template/doc_validate import-time slugs must be in D1 table."""
    from implement_admission.skill_source_table import resolve_canonical_source_uri

    for slug in (
        "implement-todo",
        "session-close-kernel",
        "session-close-audit",
        "web-transcript-preprocessing",
    ):
        uri = resolve_canonical_source_uri(slug)
        assert uri, f"{slug!r} resolved to empty uri"


@pytest.mark.offline
def test_source_uri_to_fs_line_absolute_cortex_files_root() -> None:
    line = source_uri_to_fs_line(
        "/mnt/torus/mcp-data/files/agent-skills/completion-provenance-discipline.md"
    )
    assert line == (
        'fs(sandbox="cortex", op="md_read", '
        'path="agent-skills/completion-provenance-discipline.md")'
    )


@pytest.mark.offline
def test_source_uri_to_fs_line_positional_style() -> None:
    """Positional fs_call_style for implement-packet producers (sandbox_kwargs default elsewhere)."""
    uri = "workspaces://universal-llm-gateway/.cursor/skills/foo/SKILL.md"
    assert source_uri_to_fs_line(uri, op="read", fs_call_style="positional") == (
        'fs(workspaces, op=read, path="universal-llm-gateway/.cursor/skills/foo/SKILL.md")'
    )
    cortex_uri = "cortex://agent-skills/implement-work-item.md"
    assert source_uri_to_fs_line(
        cortex_uri, op="md_read", fs_call_style="positional"
    ) == 'fs(cortex, op=md_read, path="agent-skills/implement-work-item.md")'


@pytest.mark.offline
def test_materialize_packet_sha256_deterministic_offline(
    tmp_path: Path,
) -> None:
    inject = [
        entity_slug_from_id(entity_id)
        for entity_id in coding_scope_inject_entity_ids()
    ]
    advertise = list(CODING_SESSION_ADVERTISE_SLUGS)
    skills = list(dict.fromkeys(inject + advertise))

    spec = _sample_spec(
        skills=skills,
        files_expected=["libs/implement_admission/skill_fs_line.py"],
    )

    first = materialize(spec, out_dir=tmp_path / "first")
    second = materialize(spec, out_dir=tmp_path / "second")
    assert first.packet_sha256 == second.packet_sha256


@pytest.mark.offline
def test_known_source_uris_covers_entire_coding_session_bundle() -> None:
    """Determinism guard: every coding-scope inject + advertise slug via D1 table."""
    inject = {
        entity_slug_from_id(entity_id)
        for entity_id in coding_scope_inject_entity_ids()
    }
    advertise = set(CODING_SESSION_ADVERTISE_SLUGS)
    missing = {
        slug
        for slug in (inject | advertise)
        if canonical_table_key(slug) not in CANONICAL_SKILL_SOURCE_URIS
    }
    assert not missing, (
        "coding bundle slugs fail canonical_table_key lookup: "
        f"{sorted(missing)}"
    )
