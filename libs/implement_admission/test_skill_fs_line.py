"""Tests for skill source_uri → fs line resolution (git-posture boot orientation D2)."""

from __future__ import annotations

import builtins
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from implement_admission.materialize import materialize
from implement_admission.skill_fs_line import (
    _KNOWN_SKILL_SOURCE_URIS,
    resolve_skill_source_uri,
    skill_slug_to_fs_line,
    source_uri_to_fs_line,
)
from implement_admission.test_materialize import _sample_spec


@pytest.fixture(autouse=True)
def _clear_resolve_skill_source_uri_cache() -> None:
    resolve_skill_source_uri.cache_clear()
    yield
    resolve_skill_source_uri.cache_clear()


@pytest.mark.offline
def test_source_uri_to_fs_line_workspaces() -> None:
    line = source_uri_to_fs_line(
        "workspaces://universal-llm-gateway/docs/agent-guides/skills/git-posture.md"
    )
    assert line == (
        'fs(sandbox="workspaces", op="md_read", '
        'path="universal-llm-gateway/docs/agent-guides/skills/git-posture.md")'
    )


@pytest.mark.offline
def test_source_uri_to_fs_line_cortex_scheme() -> None:
    line = source_uri_to_fs_line("cortex://agent-skills/implement-work-item.md")
    assert line == (
        'fs(sandbox="cortex", op="md_read", path="agent-skills/implement-work-item.md")'
    )


@pytest.mark.offline
def test_skill_slug_to_fs_line_known_consolidated_slug() -> None:
    line = skill_slug_to_fs_line("git-posture")
    assert 'fs(sandbox="workspaces"' in line
    assert "git-posture.md" in line


@pytest.mark.offline
def test_skill_slug_to_fs_line_unknown_slug_defaults_cortex() -> None:
    line = skill_slug_to_fs_line("custom-skill")
    assert line == (
        'fs(sandbox="cortex", op="md_read", path="agent-skills/custom-skill.md")'
    )


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
def test_source_uri_to_fs_line_positional_matches_enrich_producer() -> None:
    from systems.frontier_consult.handoff_packet_enrich import (
        source_uri_to_fs_line as enrich_line,
    )

    uri = "workspaces://universal-llm-gateway/docs/agent-guides/skills/foo.md"
    assert enrich_line(uri) == source_uri_to_fs_line(
        uri, op="read", fs_call_style="positional"
    )


@pytest.mark.offline
def test_source_uri_to_fs_line_positional_bare_slug_adds_md() -> None:
    line = source_uri_to_fs_line("git-posture", op="read", fs_call_style="positional")
    assert line == 'fs(cortex, op=read, path="agent-skills/git-posture.md")'


@pytest.mark.offline
def test_source_uri_to_fs_line_positional_cortex_scheme() -> None:
    line = source_uri_to_fs_line(
        "cortex://agent-skills/consult-routing.md",
        op="read",
        fs_call_style="positional",
    )
    assert line == 'fs(cortex, op=read, path="agent-skills/consult-routing.md")'


@pytest.mark.offline
def test_resolve_skill_source_uri_prefers_entity_get() -> None:
    with patch("implement_admission.closeout_runtime.get_runtime") as mock_rt:
        mock_rt.return_value.dispatch.return_value = {
            "id": "agent_skill:git-posture",
            "source_uri": (
                "workspaces://universal-llm-gateway/docs/agent-guides/skills/git-posture.md"
            ),
        }
        assert resolve_skill_source_uri("git-posture") == (
            "workspaces://universal-llm-gateway/docs/agent-guides/skills/git-posture.md"
        )


@pytest.mark.offline
def test_resolve_skill_source_uri_map_first_skips_closeout_import(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_import = builtins.__import__

    def block_closeout_import(
        name: str,
        globals=None,
        locals=None,
        fromlist: tuple[str, ...] = (),
        level: int = 0,
    ):
        if name == "implement_admission.closeout_runtime":
            raise ImportError("simulated offline closeout_runtime")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", block_closeout_import)
    assert resolve_skill_source_uri("git-posture") == (
        "workspaces://universal-llm-gateway/docs/agent-guides/skills/git-posture.md"
    )


@pytest.mark.offline
def test_resolve_skill_source_uri_entity_get_memoized() -> None:
    slug = "unmapped-memo-slug"
    dispatch_calls: list[tuple[str, dict[str, str]]] = []

    def dispatch(tool: str, args: dict[str, str]) -> dict[str, str]:
        dispatch_calls.append((tool, args))
        return {"source_uri": f"agent-skills/{slug}.md"}

    mock_rt = MagicMock()
    mock_rt.dispatch = dispatch

    with patch(
        "implement_admission.closeout_runtime.get_runtime", return_value=mock_rt
    ):
        resolve_skill_source_uri(slug)
        resolve_skill_source_uri(slug)
        resolve_skill_source_uri(slug)

    assert len(dispatch_calls) == 1
    assert dispatch_calls[0] == (
        "entity_get",
        {"entity_id": f"agent_skill:{slug}"},
    )


@pytest.mark.offline
def test_materialize_packet_sha256_deterministic_offline_vs_online(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_seat.inject_registry import (
        CODING_SESSION_ADVERTISE_SLUGS,
        coding_scope_inject_entity_ids,
    )

    inject = [
        entity_id.removeprefix("agent_skill:")
        for entity_id in coding_scope_inject_entity_ids()
    ]
    advertise = list(CODING_SESSION_ADVERTISE_SLUGS)
    skills = list(dict.fromkeys(inject + advertise))

    spec = _sample_spec(
        skills=skills,
        files_expected=["libs/implement_admission/skill_fs_line.py"],
    )

    online = materialize(spec, out_dir=tmp_path / "online")

    real_import = builtins.__import__

    def block_closeout_import(
        name: str,
        globals=None,
        locals=None,
        fromlist: tuple[str, ...] = (),
        level: int = 0,
    ):
        if name == "implement_admission.closeout_runtime":
            raise ImportError("simulated offline closeout_runtime")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", block_closeout_import)
    resolve_skill_source_uri.cache_clear()
    offline = materialize(spec, out_dir=tmp_path / "offline")

    assert online.packet_sha256 == offline.packet_sha256


@pytest.mark.offline
def test_known_source_uris_covers_entire_coding_session_bundle() -> None:
    """Determinism guard (CF2 / C3): every coding-scope inject + advertise slug."""
    from agent_seat.inject_registry import (
        CODING_SESSION_ADVERTISE_SLUGS,
        coding_scope_inject_entity_ids,
    )

    inject = {
        entity_id.removeprefix("agent_skill:")
        for entity_id in coding_scope_inject_entity_ids()
    }
    advertise = set(CODING_SESSION_ADVERTISE_SLUGS)
    missing = (inject | advertise) - set(_KNOWN_SKILL_SOURCE_URIS)
    assert not missing, (
        "coding bundle slugs absent from _KNOWN_SKILL_SOURCE_URIS: "
        f"{sorted(missing)} — add each with its canonical source_uri "
        "(agent-skills/<slug>.md for cortex-resident) so packet rendering stays "
        "deterministic offline vs online."
    )
