"""Tests for web handoff packet auto-enrichment (assertion #19650)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from .admission import FrontierEndpointError
from .handoff import build_pointer_body, validate_packet
from .handoff_packet_enrich import (
    EnrichResult,
    _canonical_skill_invariant_line,
    enrich_handoff_packet,
    has_densify_floor,
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
    "- Use the `consult-routing` skill "
    "(canonical slug — seat self-fetches; ¬ fs-read skill body)\n"
    "- Use the `lead-seat-boot` skill "
    "(canonical slug — seat self-fetches; ¬ fs-read skill body)"
)


class _StubCortex:
    def __init__(
        self,
        skills: dict[str, str] | None = None,
        todo_skills: list[str] | None = None,
        task_skills: list[str] | None = None,
    ) -> None:
        self.skills = skills or {}
        self.todo_skills = todo_skills or []
        self.task_skills = task_skills or []

    def entity_get(self, entity_id: str, **kwargs: Any) -> dict[str, Any]:
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


def test_canonical_skill_invariant_line_shape() -> None:
    line = _canonical_skill_invariant_line("consult-routing")
    assert "`consult-routing`" in line
    assert "canonical slug" in line
    assert "agent-skills/" not in line


def test_enrich_adds_skills_and_thread_fetch() -> None:
    cortex = _StubCortex(todo_skills=["mcp-surface-change"])
    result = enrich_handoff_packet(_THIN_WEB_PACKET, cortex=cortex)
    assert isinstance(result, EnrichResult)
    assert result.changed
    assert "mcp-surface-change" in result.skills_added
    assert "2235" in result.threads_added
    assert "skill_suggest" not in result.text.lower()
    assert "agent_bus(fetch, thread=2235" in result.text
    assert 'path="agent-skills/' not in result.text


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
    assert "`architecture-invariants`" in result.text
    assert "agent-skills/" not in result.text


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
    assert "`handoff-packet-authoring`" in result.text


def test_enrich_reports_already_wired_not_readded() -> None:
    """Pre-existing canonical slug lines land in skills_already_wired, not skills_added."""
    cortex = _StubCortex()
    first = enrich_handoff_packet(_THIN_WEB_PACKET, cortex=cortex)
    second = enrich_handoff_packet(first.text, cortex=cortex)
    for slug in ("lead-seat-boot", "consult-routing", "handoff-packet-authoring"):
        assert slug in second.skills_already_wired
        assert slug not in second.skills_added
    assert not second.changed


def test_enrich_never_injects_skill_suggest() -> None:
    cortex = _StubCortex()
    result = enrich_handoff_packet(_THIN_WEB_PACKET, cortex=cortex)
    assert "skill_suggest" not in result.text.lower()


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
    ).replace(
        "<mcp_capabilities>1. fs(read) primary file</mcp_capabilities>",
        "<mcp_capabilities>1. agent_bus(fetch, thread=2235, last=3)\n"
        "2. agent_bus(fetch, thread=2229, last=3)</mcp_capabilities>",
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


def test_validate_cursor_requires_densify_floor(tmp_path: Path) -> None:
    """P1: the densify floor binds non-web MCP seats too, not just claude-web."""
    rel = "universal-llm-gateway/tmp/reviews/cursor-no-floor.md"
    packet = _THIN_WEB_PACKET
    dest = tmp_path / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(packet, encoding="utf-8")
    with pytest.raises(FrontierEndpointError) as exc_info:
        validate_packet(
            request_id="req-cursor-floor",
            packet_path=rel,
            to_agent="claude-cursor",
            handoff_contract="consult",
            workspaces_root=tmp_path,
        )
    assert exc_info.value.code == "handoff_packet_missing_densify_floor"


def test_build_pointer_web_consult_uses_canonical_slug_priming() -> None:
    body = build_pointer_body(
        request_id="req-ptr-web",
        packet_path="tmp/reviews/foo.md",
        subject="review topic",
        pointer_body=None,
        handoff_contract="consult",
        to_agent="claude-web",
    )
    assert "canonical slug" in body
    assert "skill_suggest" not in body.lower()
    assert 'path="agent-skills/' not in body


def test_build_pointer_cursor_consult_uses_canonical_slug_priming() -> None:
    body = build_pointer_body(
        request_id="req-ptr-cursor",
        packet_path="tmp/reviews/foo.md",
        subject="review topic",
        pointer_body=None,
        handoff_contract="consult",
        to_agent="claude-cursor",
    )
    assert "canonical slug" in body
    assert "architecture-invariants.md" not in body
    assert "skill_suggest" not in body.lower()


def test_enrich_injects_arch_refs_for_cursor_parity() -> None:
    """Phase 1: enrich wires architecture-invariants + ulg-architecture by default."""
    cortex = _StubCortex()
    result = enrich_handoff_packet(_THIN_WEB_PACKET, cortex=cortex)
    assert "architecture-invariants" in result.skills_added
    assert "ulg-architecture" in result.skills_added
    assert "`architecture-invariants`" in result.text
    assert "`ulg-architecture`" in result.text
    assert "agent-skills/" not in result.text


def test_skill_slug_from_entity_accepts_rule_prefix() -> None:
    from .handoff_packet_enrich import _skill_slug_from_entity

    assert (
        _skill_slug_from_entity({"id": "rule:architecture-invariants"})
        == "architecture-invariants"
    )
    assert (
        _skill_slug_from_entity({"id": "skill:implement-work-item"})
        == "implement-work-item"
    )


def test_has_task_class_skill_ref_accepts_rule_entity_id() -> None:
    from .handoff_packet_enrich import _has_task_class_skill_ref

    text = "<mcp_capabilities>rule:mcp-surface-change</mcp_capabilities>"
    assert _has_task_class_skill_ref(text) is True


def test_enrich_recognizes_legacy_fs_lines_as_already_wired() -> None:
    """Legacy fs-line packets must not get duplicate canonical slug lines."""
    legacy = _THIN_WEB_PACKET.replace(
        "<invariants>[scope] traces to task.</invariants>",
        (
            "<invariants>[scope] traces to task.\n"
            '- fs(cortex, op=read, path="agent-skills/consult-routing.md")'
            "  # agent_skill:consult-routing</invariants>"
        ),
    )
    cortex = _StubCortex()
    result = enrich_handoff_packet(legacy, cortex=cortex)
    assert "consult-routing" in result.skills_already_wired
    assert "consult-routing" not in result.skills_added
    invariants = result.text.split("<invariants>")[1].split("</invariants>")[0]
    assert "`consult-routing`" not in invariants
