"""Tests for web handoff packet auto-enrichment (assertion #19650)."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest
from agent_seat.inject_budget import HANDOFF_INLINE_BUDGET_BYTES
from implement_admission.skill_delivery_channels import (
    InlineBodyResolution,
    SkillInlineBudgetExceeded,
    enforce_inline_budget,
    format_inline_skill_block,
    parse_inline_skill_blocks,
    resolve_inline_bodies,
    text_without_inline_payload_regions,
    validate_exactly_one_skill_channel,
    validate_inline_skill_hashes,
)

from .admission import FrontierEndpointError
from .handoff import build_pointer_body, validate_packet
from .handoff_life_mirror import mirror_workspaces_pointers_for_web
from .handoff_packet_enrich import (
    EnrichResult,
    _canonical_skill_invariant_line,
    enrich_handoff_packet,
    has_densify_floor,
)

_THIN_WEB_PACKET = """\
---
active_project_tag: project:test
cortex_brief_confirmed: true
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
    assert "docstring-quality" in result.skills_added
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


def test_enrich_injects_reasoning_posture_on_consult() -> None:
    cortex = _StubCortex()
    result = enrich_handoff_packet(_THIN_WEB_PACKET, cortex=cortex)
    assert "reasoning-posture" in result.skills_added
    assert "`reasoning-posture`" in result.text


def test_enrich_injects_reasoning_posture_from_route_contract() -> None:
    """Route-derived contract wins when the packet omits frontmatter contract."""
    packet = _THIN_WEB_PACKET.replace("contract: consult\n", "")
    cortex = _StubCortex()
    skipped = enrich_handoff_packet(packet, cortex=cortex)
    assert "reasoning-posture" not in skipped.skills_added
    injected = enrich_handoff_packet(
        packet, cortex=cortex, handoff_contract="light-bounded"
    )
    assert "reasoning-posture" in injected.skills_added


def test_enrich_skips_reasoning_posture_on_implement() -> None:
    packet = _THIN_WEB_PACKET.replace("contract: consult", "contract: implement")
    cortex = _StubCortex()
    result = enrich_handoff_packet(packet, cortex=cortex)
    assert "reasoning-posture" not in result.skills_added
    assert "reasoning-posture" not in result.skills_already_wired
    routed = enrich_handoff_packet(
        packet.replace("contract: implement\n", ""),
        cortex=cortex,
        handoff_contract="implement",
    )
    assert "reasoning-posture" not in routed.skills_added


def test_enrich_reports_already_wired_not_readded() -> None:
    """Pre-existing slug lines land in skills_already_wired, not skills_added."""
    cortex = _StubCortex()
    first = enrich_handoff_packet(_THIN_WEB_PACKET, cortex=cortex)
    second = enrich_handoff_packet(first.text, cortex=cortex)
    for slug in (
        "lead-seat-boot",
        "consult-routing",
        "handoff-packet-authoring",
        "reasoning-posture",
    ):
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
        "<mcp_capabilities>LIFE/CORTEX MCP: ON\n"
        "CODE/VORTEX MCP: OFF\n"
        "1. agent_bus(fetch, thread=2235, last=3)\n"
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


def test_validate_web_anthropic_alias_skips_arch_refs(tmp_path: Path) -> None:
    """Canonical bus address must take the same web densify path (a24046)."""
    rel = "universal-llm-gateway/tmp/reviews/web-anthropic-thin.md"
    packet = _THIN_WEB_PACKET.replace(
        "<invariants>[scope] traces to task.</invariants>",
        f"<invariants>[scope] traces to task.\n{_DENSIFY_INVARIANTS}</invariants>",
    ).replace(
        "<mcp_capabilities>1. fs(read) primary file</mcp_capabilities>",
        "<mcp_capabilities>LIFE/CORTEX MCP: ON\n"
        "CODE/VORTEX MCP: OFF\n"
        "1. agent_bus(fetch, thread=2235, last=3)\n"
        "2. agent_bus(fetch, thread=2229, last=3)</mcp_capabilities>",
    )
    dest = tmp_path / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(packet, encoding="utf-8")
    validate_packet(
        request_id="req-web-anthropic-skip-arch",
        packet_path=rel,
        to_agent="web-anthropic",
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
        packet_path="cortex://ephemeral/handoffs/foo.md",
        subject="review topic",
        pointer_body=None,
        handoff_contract="consult",
        to_agent="claude-web",
    )
    assert "LIFE/CORTEX MCP ON" in body
    assert "CODE/VORTEX MCP OFF" in body
    assert "skill_suggest" not in body.lower()
    assert 'path="agent-skills/' not in body
    assert 'sandbox="workspaces"' not in body
    assert "cortex://" in body
    assert "bus reply" in body


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
    """Phase 1: enrich wires ULG code-work floor triple by default."""
    cortex = _StubCortex()
    result = enrich_handoff_packet(_THIN_WEB_PACKET, cortex=cortex)
    assert "architecture-invariants" in result.skills_added
    assert "ulg-architecture" in result.skills_added
    assert "docstring-quality" in result.skills_added
    assert "`architecture-invariants`" in result.text
    assert "`ulg-architecture`" in result.text
    assert "`docstring-quality`" in result.text
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
    invariants = result.text.split("<invariants>", 1)[1].rsplit("</invariants>", 1)[0]
    assert "`consult-routing`" not in invariants


def test_enrich_inline_authoritative_materializes_allowlist_skills() -> None:
    cortex = _StubCortex(todo_skills=["mcp-surface-change"])
    result = enrich_handoff_packet(
        _THIN_WEB_PACKET,
        cortex=cortex,
        to_agent="claude-web",
        skill_delivery="inline_authoritative",
    )
    assert result.inline_materialized
    assert "mcp-surface-change" in result.inline_slugs
    assert "lead-seat-boot" in result.skills_added
    blocks = parse_inline_skill_blocks(result.text)
    inlined = {block.slug for block in blocks}
    assert "consult-routing" in inlined
    assert "handoff-packet-authoring" in inlined
    assert "architecture-invariants" in inlined
    assert validate_exactly_one_skill_channel(result.text) is None
    assert validate_inline_skill_hashes(result.text) is None


def test_enrich_inline_rewrites_pointer_lines_to_orientation() -> None:
    packet = _THIN_WEB_PACKET.replace(
        "<invariants>[scope] traces to task.</invariants>",
        (
            "<invariants>[scope] traces to task.\n"
            "- Use the `consult-routing` skill "
            "(canonical slug — seat self-fetches; ¬ fs-read skill body)</invariants>"
        ),
    )
    cortex = _StubCortex()
    result = enrich_handoff_packet(
        packet,
        cortex=cortex,
        skill_delivery="inline_authoritative",
    )
    invariants = result.text.split("<invariants>", 1)[1].rsplit("</invariants>", 1)[0]
    assert "bodies inlined below" in invariants
    scan = text_without_inline_payload_regions(result.text)
    assert "- Use the `consult-routing` skill (canonical slug" not in scan
    assert "Use the `lead-seat-boot` skill" in invariants


def test_enrich_inline_removes_task_guidance_skill_pointer() -> None:
    packet = _THIN_WEB_PACKET.replace(
        "<task_guidance>Review risks.</task_guidance>",
        "<task_guidance>Use the `consult-routing` skill.</task_guidance>",
    )
    result = enrich_handoff_packet(
        packet,
        cortex=_StubCortex(),
        skill_delivery="inline_authoritative",
    )
    assert "Use the `consult-routing` skill" not in text_without_inline_payload_regions(
        result.text
    )
    assert validate_exactly_one_skill_channel(result.text) is None


def test_enrich_inline_idempotent_and_rebuilds_stale_digest() -> None:
    cortex = _StubCortex()
    first = enrich_handoff_packet(
        _THIN_WEB_PACKET,
        cortex=cortex,
        skill_delivery="inline_authoritative",
    )
    second = enrich_handoff_packet(
        first.text,
        cortex=cortex,
        skill_delivery="inline_authoritative",
    )
    assert not second.changed
    assert second.skills_inlined == []
    blocks = parse_inline_skill_blocks(first.text)
    stale = first.text.replace(blocks[0].digest, "sha256:deadbeef", 1)
    third = enrich_handoff_packet(
        stale,
        cortex=cortex,
        skill_delivery="inline_authoritative",
    )
    assert third.changed
    assert blocks[0].slug in third.skills_inlined
    assert validate_inline_skill_hashes(third.text) is None


def test_enrich_non_web_byte_identical_without_skill_delivery() -> None:
    cortex = _StubCortex(todo_skills=["mcp-surface-change"])

    def _run(to_agent: str | None) -> EnrichResult:
        return enrich_handoff_packet(
            _THIN_WEB_PACKET,
            cortex=copy.deepcopy(cortex),
            to_agent=to_agent,
        )

    baseline = _run(None)
    cursor = _run("claude-cursor")
    assert baseline.text == cursor.text
    assert baseline.skills_added == cursor.skills_added


def _synthetic_inline_resolution(slug: str, byte_len: int) -> InlineBodyResolution:
    body = "x" * byte_len
    return InlineBodyResolution(
        slug=slug,
        body=body,
        digest="sha256:0000000000000000000000000000000000000000000000000000000000000000",
        source_uri="workspaces://universal-llm-gateway/.cursor/skills/x/SKILL.md",
        rev="table:test",
        byte_len=byte_len,
    )


def test_enforce_inline_budget_admits_at_exact_limit() -> None:
    assert HANDOFF_INLINE_BUDGET_BYTES == 131_072
    half = HANDOFF_INLINE_BUDGET_BYTES // 2
    enforce_inline_budget(
        [
            _synthetic_inline_resolution("a", half),
            _synthetic_inline_resolution("b", half),
        ],
        HANDOFF_INLINE_BUDGET_BYTES,
    )


def test_enforce_inline_budget_rejects_one_byte_over_limit() -> None:
    assert HANDOFF_INLINE_BUDGET_BYTES == 131_072
    half = HANDOFF_INLINE_BUDGET_BYTES // 2
    with pytest.raises(SkillInlineBudgetExceeded) as exc_info:
        enforce_inline_budget(
            [
                _synthetic_inline_resolution("a", half),
                _synthetic_inline_resolution("b", half + 1),
            ],
            HANDOFF_INLINE_BUDGET_BYTES,
        )
    assert exc_info.value.total_bytes == HANDOFF_INLINE_BUDGET_BYTES + 1
    assert exc_info.value.total_bytes == 131_073
    assert exc_info.value.budget_bytes == HANDOFF_INLINE_BUDGET_BYTES


def test_enrich_inline_budget_exceeded() -> None:
    resolved = resolve_inline_bodies(
        (
            "architecture-invariants",
            "ulg-architecture",
            "consult-routing",
            "handoff-packet-authoring",
            "implement-work-item",
            "implement-todo",
            "modularize-discipline",
            "mcp-surface-change",
            "build-pipeline",
            "debug-with-events",
            "service-lifecycle",
            "dispatch-workflow",
        )
    )
    with pytest.raises(SkillInlineBudgetExceeded):
        enforce_inline_budget(resolved, budget_bytes=1)


def test_validate_inline_rejects_missing_closing_fence() -> None:
    resolved = resolve_inline_bodies(("consult-routing",))
    block = format_inline_skill_block(
        resolved[0].slug,
        source_uri=resolved[0].source_uri,
        rev=resolved[0].rev,
        body=resolved[0].body,
    )
    open_fence = block.index("```markdown\n") + len("```markdown\n")
    text = f"<invariants>[scope]</invariants>{block[:open_fence]}"
    violation = validate_inline_skill_hashes(text)
    assert violation is not None
    assert violation.code == "skill_inline_malformed"


def test_validate_inline_rejects_duplicate_marker() -> None:
    resolved = resolve_inline_bodies(("consult-routing",))
    block = format_inline_skill_block(
        resolved[0].slug,
        source_uri=resolved[0].source_uri,
        rev=resolved[0].rev,
        body=resolved[0].body,
    )
    text = f"<invariants>[scope]</invariants>{block}{block}"
    violation = validate_inline_skill_hashes(text)
    assert violation is not None
    assert violation.code == "skill_inline_malformed"


def test_validate_inline_rejects_altered_payload() -> None:
    resolved = resolve_inline_bodies(("consult-routing",))
    block = format_inline_skill_block(
        resolved[0].slug,
        source_uri=resolved[0].source_uri,
        rev=resolved[0].rev,
        body=resolved[0].body,
    )
    tampered = block.replace("TAMPER_ME", "TAMPER_ME", 1)
    payload_start = tampered.index("```markdown\n") + len("```markdown\n")
    close = tampered.index("\n```", payload_start)
    tampered = (
        tampered[:payload_start]
        + "CORRUPTED\n"
        + tampered[payload_start:close]
        + tampered[close:]
    )
    violation = validate_inline_skill_hashes(
        f"<invariants>[scope]</invariants>{tampered}"
    )
    assert violation is not None
    assert violation.code in {"skill_hash_mismatch", "skill_inline_malformed"}


def test_validate_dual_channel_ignores_body_local_skill_refs() -> None:
    resolved = resolve_inline_bodies(("handoff-packet-authoring",))
    block = format_inline_skill_block(
        resolved[0].slug,
        source_uri=resolved[0].source_uri,
        rev=resolved[0].rev,
        body=resolved[0].body,
    )
    text = f"<invariants>[scope]</invariants>{block}"
    assert validate_exactly_one_skill_channel(text) is None


def test_validate_dual_channel_rejects_outer_pointer_for_inlined_slug() -> None:
    resolved = resolve_inline_bodies(("consult-routing",))
    block = format_inline_skill_block(
        resolved[0].slug,
        source_uri=resolved[0].source_uri,
        rev=resolved[0].rev,
        body=resolved[0].body,
    )
    text = (
        f"<invariants>[scope]\n"
        "- Use the `consult-routing` skill "
        "(canonical slug — seat self-fetches; ¬ fs-read skill body)\n"
        f"{block}</invariants>"
    )
    violation = validate_exactly_one_skill_channel(text)
    assert violation is not None
    assert violation.code == "skill_dual_channel"


def test_mirror_preserves_skill_inline_source_uri() -> None:
    resolved = resolve_inline_bodies(("consult-routing",))
    block = format_inline_skill_block(
        resolved[0].slug,
        source_uri=resolved[0].source_uri,
        rev=resolved[0].rev,
        body=resolved[0].body,
    )
    packet = (
        "<corpus>spec at workspaces://universal-llm-gateway/tmp/reviews/foo.md</corpus>"
        f"<invariants>{block}</invariants>"
    )
    mirrored, rewrites = mirror_workspaces_pointers_for_web(packet)
    assert rewrites
    # Census skills resolve under cursor-plugins/… (plugin SoT), not hub .cursor/.
    assert resolved[0].source_uri in mirrored
    assert validate_inline_skill_hashes(mirrored) is None


def test_build_pointer_includes_skill_inline_gate_line() -> None:
    body = build_pointer_body(
        request_id="req-inline-ptr",
        packet_path="cortex://ephemeral/handoffs/foo.md",
        subject="review topic",
        pointer_body=None,
        handoff_contract="consult",
        to_agent="claude-web",
        skill_inline_materialized=True,
    )
    assert "Skill-inline gate" in body
    assert "load before findings" in body


def test_enrich_web_anthropic_bus_address_materializes_inline() -> None:
    """Canonical bus address web-anthropic must hit inline_authoritative enrich."""
    cortex = _StubCortex()
    result = enrich_handoff_packet(
        _THIN_WEB_PACKET,
        cortex=cortex,
        to_agent="web-anthropic",
        skill_delivery="inline_authoritative",
    )
    assert result.inline_materialized
    blocks = parse_inline_skill_blocks(result.text)
    assert blocks
    assert validate_inline_skill_hashes(result.text) is None


def test_validate_enriched_inline_packet(tmp_path: Path) -> None:
    cortex = _StubCortex(todo_skills=["mcp-surface-change"])
    enriched = enrich_handoff_packet(
        _THIN_WEB_PACKET,
        cortex=cortex,
        to_agent="claude-web",
        skill_delivery="inline_authoritative",
    )
    rel = "universal-llm-gateway/tmp/reviews/web-inline.md"
    dest = tmp_path / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(enriched.text, encoding="utf-8")
    validate_packet(
        request_id="req-web-inline",
        packet_path=rel,
        to_agent="claude-web",
        handoff_contract="consult",
        workspaces_root=tmp_path,
    )


def test_enrich_web_defaults_life_on_code_off() -> None:
    """Life/web handoff stamps the split and keeps life-safe thread fetches."""
    from .handoff_web_mcp_default import (
        LIFE_ONLY_MCP_BODY,
        has_explicit_life_code_split,
    )

    cortex = _StubCortex(todo_skills=["mcp-surface-change"])
    result = enrich_handoff_packet(
        _THIN_WEB_PACKET,
        cortex=cortex,
        to_agent="web-anthropic",
    )
    body = result.text
    mcp = None
    start = body.find("<mcp_capabilities>")
    end = body.find("</mcp_capabilities>")
    assert start >= 0 and end > start
    mcp = body[start + len("<mcp_capabilities>") : end]
    assert has_explicit_life_code_split(mcp)
    assert "NONE" not in mcp
    assert LIFE_ONLY_MCP_BODY.splitlines()[0] in mcp
    assert "Life-surface writes require explicit task/output authority." in mcp
    assert "agent_bus(fetch, thread=2235" in mcp
    assert "2235" in result.threads_added
    assert has_densify_floor(result.text)


def test_validate_web_rejects_ambiguous_mcp_grant(tmp_path: Path) -> None:
    """Direct validation rejects generic MCP wording before other floor checks."""
    rel = "universal-llm-gateway/tmp/reviews/web-ambiguous-mcp.md"
    packet = _THIN_WEB_PACKET.replace(
        "1. fs(read) primary file",
        "You have MCP. Cite tool calls.",
    )
    dest = tmp_path / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(packet, encoding="utf-8")
    with pytest.raises(FrontierEndpointError) as exc_info:
        validate_packet(
            request_id="req-web-ambiguous-mcp",
            packet_path=rel,
            to_agent="claude-web",
            handoff_contract="consult",
            workspaces_root=tmp_path,
        )
    assert exc_info.value.code == "handoff_packet_web_mcp_split_required"


def test_has_densify_floor_requires_fetch_for_life_mcp() -> None:
    packet = f"""---
related_thread_ids: ["99"]
---
<invariants>{_DENSIFY_INVARIANTS}</invariants>
<mcp_capabilities>
LIFE/CORTEX MCP: ON — cortex(entity_get/search).
CODE/VORTEX MCP: OFF — no workspaces or code-only tools.
</mcp_capabilities>
"""
    assert not has_densify_floor(packet)
