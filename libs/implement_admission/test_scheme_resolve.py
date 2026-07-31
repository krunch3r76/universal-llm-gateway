"""Round-trip tests for cortex/workspaces scheme packet resolution."""

from __future__ import annotations

from pathlib import Path

import pytest

from implement_admission.admission_read import read_packet
from implement_admission.scheme_resolve import (
    parse_schemed_path,
    resolve_schemed_packet,
    resolve_schemed_packet_file,
)
from implement_admission.source_ref import SourceRefError


def _six_block_packet(source_ref: str) -> str:
    return f"""---
source_ref: {source_ref}
---
<scope>scope</scope>
<invariants>invariants</invariants>
<task_guidance>acceptance criteria listed</task_guidance>
<mcp_capabilities>fs(cortex)</mcp_capabilities>
<output_format>closeout</output_format>
<corpus>corpus</corpus>
"""


@pytest.fixture
def sandbox_roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    ws = tmp_path / "workspaces"
    cortex = tmp_path / "cortex"
    ws.mkdir()
    cortex.mkdir()
    monkeypatch.setenv("PROJECT_ROOT", str(ws))
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(cortex))
    return ws, cortex


@pytest.mark.parametrize(
    "scheme_path",
    [
        "cortex:notes/system/specs/example.md",
        "cortex://notes/system/specs/example.md",
        "packet:cortex:notes/system/specs/example.md",
    ],
)
@pytest.mark.offline
def test_cortex_scheme_round_trip(
    sandbox_roots: tuple[Path, Path],
    scheme_path: str,
) -> None:
    ws, cortex = sandbox_roots
    rel = "notes/system/specs/example.md"
    target = cortex / rel
    target.parent.mkdir(parents=True)
    target.write_text(_six_block_packet(scheme_path), encoding="utf-8")

    parsed = parse_schemed_path(scheme_path)
    assert parsed.scheme == "cortex"
    assert parsed.rel_path == rel

    resolved = resolve_schemed_packet_file(scheme_path)
    assert resolved == target

    packet = read_packet(scheme_path, workspaces_root=ws)
    assert "acceptance criteria listed" in packet.text
    assert str(resolved) == packet.resolved_path


@pytest.mark.parametrize(
    "scheme_path",
    [
        "universal-llm-gateway/tmp/packets/ws-example.md",
        "workspaces:universal-llm-gateway/tmp/packets/ws-example.md",
        "workspaces://universal-llm-gateway/tmp/packets/ws-example.md",
        "ws:universal-llm-gateway/tmp/packets/ws-example.md",
        "ws://universal-llm-gateway/tmp/packets/ws-example.md",
    ],
)
@pytest.mark.offline
def test_workspaces_scheme_regression(
    sandbox_roots: tuple[Path, Path],
    scheme_path: str,
) -> None:
    ws, _cortex = sandbox_roots
    rel = "universal-llm-gateway/tmp/packets/ws-example.md"
    target = ws / rel
    target.parent.mkdir(parents=True)
    target.write_text(_six_block_packet("todo:ws-example"), encoding="utf-8")

    resolved = resolve_schemed_packet_file(scheme_path, workspaces_root_override=ws)
    assert resolved == target

    packet = read_packet(scheme_path, workspaces_root=ws)
    assert packet.resolved_path == str(target)


@pytest.mark.offline
def test_bare_cortex_file_root_resolves_under_cortex(
    sandbox_roots: tuple[Path, Path],
) -> None:
    """Bare notes/... must not resolve under workspaces (friction 23230)."""
    _ws, cortex = sandbox_roots
    rel = "notes/system/specs/bare-notes.md"
    target = cortex / rel
    target.parent.mkdir(parents=True)
    target.write_text("dense-spec body\n", encoding="utf-8")

    resolution = resolve_schemed_packet(rel)
    assert resolution.parsed.scheme == "cortex"
    assert resolution.resolved_file == target

    assert resolve_schemed_packet_file(rel) == target
    assert resolve_schemed_packet_file(f"cortex://{rel}") == target


@pytest.mark.offline
def test_bare_tasks_specs_still_resolves_under_workspaces(
    sandbox_roots: tuple[Path, Path],
) -> None:
    ws, _cortex = sandbox_roots
    rel = "universal-llm-gateway/tasks/specs/ws-only.md"
    target = ws / rel
    target.parent.mkdir(parents=True)
    target.write_text("workspace dense-spec\n", encoding="utf-8")

    resolution = resolve_schemed_packet(
        "tasks/specs/ws-only.md",
        workspaces_root_override=ws,
    )
    assert resolution.parsed.scheme is None
    assert resolution.resolved_file == target


@pytest.mark.offline
def test_cortex_containment_rejects_parent_traversal(
    sandbox_roots: tuple[Path, Path],
) -> None:
    ws, cortex = sandbox_roots
    (cortex / "notes").mkdir(parents=True)
    (cortex / "secret.md").write_text("secret", encoding="utf-8")

    with pytest.raises(SourceRefError) as exc_info:
        read_packet("cortex:notes/../secret.md", workspaces_root=ws)
    assert exc_info.value.code == "handoff_packet_invalid"

    resolution = resolve_schemed_packet("cortex:notes/../secret.md")
    assert resolution.resolved_file is None


@pytest.mark.offline
def test_cortex_read_in_place_not_materialized(
    sandbox_roots: tuple[Path, Path],
) -> None:
    ws, cortex = sandbox_roots
    rel = "notes/system/specs/in-place.md"
    target = cortex / rel
    target.parent.mkdir(parents=True)
    original = _six_block_packet("packet:cortex:notes/system/specs/in-place.md")
    target.write_text(original, encoding="utf-8")

    read_packet("cortex:notes/system/specs/in-place.md", workspaces_root=ws)
    assert target.read_text(encoding="utf-8") == original
    assert not (ws / rel).exists()
