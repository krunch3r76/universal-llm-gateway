"""Entity-pointer skill delivery lines (skills-delivery ruling 2026-11-10 leg A)."""

from __future__ import annotations

import pytest

from implement_admission.skill_delivery_channels import (
    render_skill_entity_pointer,
    validate_exactly_one_skill_channel,
)


@pytest.mark.offline
def test_render_skill_entity_pointer_shape() -> None:
    line = render_skill_entity_pointer("consult-routing", bullet=True)
    assert "Skill `consult-routing`" in line
    assert "agent_skill:consult-routing" in line
    assert "workspaces://universal-llm-gateway/" in line
    assert "md_list, then md_read" in line
    assert "Use the `consult-routing` skill" not in line


@pytest.mark.offline
def test_pointer_only_packet_passes_exactly_one_channel() -> None:
    line = render_skill_entity_pointer("lead-seat-boot", bullet=True)
    packet = f"<invariants>[scope]\n{line}\n</invariants>"
    assert validate_exactly_one_skill_channel(packet) is None


@pytest.mark.offline
def test_index_marker_overflow_no_use_line() -> None:
    block = render_skill_entity_pointer(
        "architecture-invariants",
        "agent_skill:architecture-invariants",
        index_marker=True,
    )
    assert "injected-index:architecture-invariants" in block
    assert "md_list, then md_read" in block
    assert "Use the `architecture-invariants` skill" not in block
