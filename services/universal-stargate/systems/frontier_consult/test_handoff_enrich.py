"""Tests for web handoff packet auto-enrichment (assertion #19650)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from .admission import FrontierEndpointError
from .handoff import build_pointer_body, validate_packet
from .handoff_packet_enrich import (
    EnrichResult,
    enrich_handoff_packet,
    has_densify_floor,
    source_uri_to_fs_line,
)

_THIN_WEB_PACKET = """\
---
active_project_tag: project:test
cortex_boot_confirmed: true
related_thread_ids: ["2235", "2229"]
todo: todo:sample-todo
contract: consult
---

<scope>Stargate MCP routing surface change.</scope>
<invariants>[scope] traces to task.</invariants>
<task_guidance>Review risks.</task_guidance>
<corpus>artifact</corpus>
<mcp_capabilities>1. fs(read) primary file</mcp_capabilities>
<output_format>Reply on thread.</output_format>
"""

_DENSIFY_INVARIANTS = (
    '- fs(cortex, op=read, path="agent-skills/consult-routing.md")\n'
    '- fs(cortex, op=read, path="agent-skills/lead-seat-boot.md")'
)


class _StubCortex:
    def __init__(
        self,
        skills: dict[str, str] | None = None,
        todo_skills: list[str] | None = None,
        task_skills: list[str] | None = None,
    ) -> None:
        self.skills = skills or {
            "lead-seat-boot": "agent-skills/lead-seat-boot.md",
            "consult-routing": "agent-skills/consult-routing.md",
            "handoff-packet-authoring": "workspaces://universal-llm-gateway/docs/agent-guides/skills/handoff-packet-authoring.md",
            "mcp-surface-change": "agent-skills/mcp-surface-change.md",
            "debug-with-events": "agent-skills/debug-with-events.md",
            "architecture-invariants": "workspaces://universal-llm-gateway/docs/agent-guides/skills/architecture-invariants.md",
            "ulg-architecture": "workspaces://universal-llm-gateway/docs/agent-guides/skills/ulg-architecture.md",
        }
        self.todo_skills = todo_skills or []
        self.task_skills = task_skills or []

    def entity_get(self, entity_id: str, **kwargs: Any) -> dict[str, Any]:
        if entity_id.startswith("agent_skill:"):
            slug = entity_id.removeprefix("agent_skill:")
            uri = self.skills.get(slug, "")
            return {
                "id": entity_id,
                "source_uri": uri,
                "attributes": None,
            }
        if entity_id.startswith("todo:"):
            return {
                "id": entity_id,
                "attributes": {"required_skills": self.todo_skills},
            }
        if entity_id.startswith("task:"):
            return {
                "id": entity_id,
                "attributes": {"required_skills": self.task_skills},
            }
        raise KeyError(entity_id)


def test_source_uri_to_fs_line_cortex_relative() -> None:
    line = source_uri_to_fs_line("agent-skills/mcp-surface-change.md")
    assert line == 'fs(cortex, op=read, path="agent-skills/mcp-surface-change.md")'


def test_source_uri_to_fs_line_workspaces_uri() -> None:
    line = source_uri_to_fs_line(
        "workspaces://universal-llm-gateway/docs/agent-guides/skills/foo.md"
    )
    assert "fs(workspaces, op=read" in line
    assert "universal-llm-gateway/docs" in line


def test_resolve_source_uri_top_level_shape() -> None:
    """source_uri returned at top level (SF1), not only in attributes."""

    class TopLevelCortex:
        def entity_get(self, entity_id: str, **kwargs: Any) -> dict[str, Any]:
            return {
                "id": entity_id,
                "source_uri": "agent-skills/consult-routing.md",
                "attributes": None,
            }

    from .handoff_packet_enrich import _resolve_source_uri

    assert (
        _resolve_source_uri(TopLevelCortex(), "consult-routing")
        == "agent-skills/consult-routing.md"
    )


def test_enrich_adds_skills_and_thread_fetch() -> None:
    cortex = _StubCortex(todo_skills=["mcp-surface-change"])
    result = enrich_handoff_packet(_THIN_WEB_PACKET, cortex=cortex)
    assert isinstance(result, EnrichResult)
    assert result.changed
    assert "mcp-surface-change" in result.skills_added
    assert "2235" in result.threads_added
    assert "skill_suggest" in result.text
    assert "agent_bus(fetch, thread=2235" in result.text


def test_enrich_merges_task_frontmatter_required_skills() -> None:
    packet = """\
---
active_project_tag: project:test
task: task:sample-task
contract: consult
---

<scope>ULG MCP routing surface change.</scope>
<invariants>[scope] traces to task.</invariants>
<task_guidance>Review risks.</task_guidance>
<corpus>artifact</corpus>
<mcp_capabilities>1. fs(read) primary file</mcp_capabilities>
<output_format>Reply on thread.</output_format>
"""
    cortex = _StubCortex(task_skills=["architecture-invariants", "ulg-architecture"])
    result = enrich_handoff_packet(packet, cortex=cortex)
    assert "architecture-invariants" in result.skills_added
    assert "ulg-architecture" in result.skills_added
    assert "architecture-invariants.md" in result.text


def test_enrich_idempotent() -> None:
    cortex = _StubCortex()
    first = enrich_handoff_packet(_THIN_WEB_PACKET, cortex=cortex)
    second = enrich_handoff_packet(first.text, cortex=cortex)
    assert not second.changed


def test_enrich_injects_handoff_packet_authoring_by_default() -> None:
    """AC1/AC8: handoff-packet-authoring is a default densify slug."""
    cortex = _StubCortex()
    result = enrich_handoff_packet(_THIN_WEB_PACKET, cortex=cortex)
    assert "handoff-packet-authoring" in result.skills_added
    assert "handoff-packet-authoring" in result.text


def test_enrich_reports_already_wired_not_readded() -> None:
    """AC2/AC8: pre-existing fs-lines land in skills_already_wired, not skills_added."""
    cortex = _StubCortex()
    first = enrich_handoff_packet(_THIN_WEB_PACKET, cortex=cortex)
    second = enrich_handoff_packet(first.text, cortex=cortex)
    # Second pass: the default slugs are now present and must not be re-added.
    for slug in ("lead-seat-boot", "consult-routing", "handoff-packet-authoring"):
        assert slug in second.skills_already_wired
        assert slug not in second.skills_added
    assert not second.changed  # already-wired is informational; idempotency holds


def test_injected_skill_suggest_step_omits_loaded() -> None:
    """AC5/AC8: injected skill_suggest step passes conversation_context only."""
    cortex = _StubCortex()
    result = enrich_handoff_packet(_THIN_WEB_PACKET, cortex=cortex)
    assert "skill_suggest" in result.text
    assert "loaded=[" not in result.text


def test_has_densify_floor_requires_task_class() -> None:
    assert not has_densify_floor("<scope>x</scope>")
    assert has_densify_floor(f"<invariants>{_DENSIFY_INVARIANTS}</invariants>")


def test_has_densify_floor_requires_fetch_when_threads_set() -> None:
    packet = f"""---
related_thread_ids: ["99"]
---
<invariants>{_DENSIFY_INVARIANTS}</invariants>
<mcp_capabilities>1. investigate</mcp_capabilities>
"""
    assert not has_densify_floor(packet)
    packet_with_fetch = packet.replace(
        "1. investigate",
        "1. agent_bus(fetch, thread=99, last=3)",
    )
    assert has_densify_floor(packet_with_fetch)


def test_validate_web_skips_arch_refs_with_densify_floor(tmp_path: Path) -> None:
    rel = "universal-llm-gateway/tmp/reviews/web-thin.md"
    packet = _THIN_WEB_PACKET.replace(
        "<invariants>[scope] traces to task.</invariants>",
        f"<invariants>[scope] traces to task.\n{_DENSIFY_INVARIANTS}</invariants>",
    ).replace(
        "<mcp_capabilities>1. fs(read) primary file</mcp_capabilities>",
        "<mcp_capabilities>1. agent_bus(fetch, thread=2235, last=3)\n"
        "2. agent_bus(fetch, thread=2229, last=3)</mcp_capabilities>",
    )
    dest = tmp_path / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(packet, encoding="utf-8")
    validate_packet(
        request_id="req-web-skip-arch",
        packet_path=rel,
        to_agent="claude-web",
        handoff_contract="consult",
        workspaces_root=tmp_path,
    )


def test_validate_cursor_still_requires_arch_refs(tmp_path: Path) -> None:
    rel = "universal-llm-gateway/tmp/reviews/cursor-thin.md"
    packet = _THIN_WEB_PACKET.replace(
        "<invariants>[scope] traces to task.</invariants>",
        f"<invariants>[scope] traces to task.\n{_DENSIFY_INVARIANTS}</invariants>",
    )
    dest = tmp_path / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(packet, encoding="utf-8")
    with pytest.raises(FrontierEndpointError) as exc_info:
        validate_packet(
            request_id="req-cursor-arch",
            packet_path=rel,
            to_agent="claude-cursor",
            handoff_contract="consult",
            workspaces_root=tmp_path,
        )
    assert exc_info.value.code == "handoff_packet_missing_arch_skillrefs"


def test_build_pointer_web_consult_uses_checklist_not_arch_read() -> None:
    body = build_pointer_body(
        request_id="req-ptr-web",
        packet_path="tmp/reviews/foo.md",
        subject="review topic",
        pointer_body=None,
        handoff_contract="consult",
        to_agent="claude-web",
    )
    assert "web-receiver priming checklist" in body
    assert "server-injected on boot" not in body
    assert "architecture-invariants.md" not in body


def test_build_pointer_cursor_consult_keeps_arch_read() -> None:
    body = build_pointer_body(
        request_id="req-ptr-cursor",
        packet_path="tmp/reviews/foo.md",
        subject="review topic",
        pointer_body=None,
        handoff_contract="consult",
        to_agent="claude-cursor",
    )
    assert "architecture-invariants.md" in body


def test_enrich_injects_arch_refs_for_cursor_parity() -> None:
    """Phase 1: enrich wires architecture-invariants + ulg-architecture by default.

    Cursor handoff packets satisfy the arch-ref floor via enrich (friction 20979).
    """
    cortex = _StubCortex()
    result = enrich_handoff_packet(_THIN_WEB_PACKET, cortex=cortex)
    assert "architecture-invariants" in result.skills_added
    assert "ulg-architecture" in result.skills_added
    assert "architecture-invariants.md" in result.text
    assert "ulg-architecture.md" in result.text
