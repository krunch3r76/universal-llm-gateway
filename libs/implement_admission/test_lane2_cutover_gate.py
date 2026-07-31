"""Tests for Lane-2 cross-lane boot-read cutover gate (AC18)."""

from __future__ import annotations

import pytest

from implement_admission.lane2_cutover_gate import (
    BootReadCutoverError,
    assert_boot_read_cutover_allowed,
    cutover_status,
    prefer_inject_entity_id,
)


@pytest.mark.offline
def test_orchestrator_core_cutover_eligible() -> None:
    status = cutover_status("orchestrator-core")
    assert status.eligible
    assert status.evidence_uri is not None
    assert_boot_read_cutover_allowed("rule:orchestrator-core")


@pytest.mark.offline
def test_architecture_invariants_cutover_blocked() -> None:
    with pytest.raises(BootReadCutoverError):
        assert_boot_read_cutover_allowed("agent_skill:architecture-invariants")


@pytest.mark.offline
def test_prefer_inject_keeps_rule_without_cutover() -> None:
    assert prefer_inject_entity_id("rule:architecture-invariants") == (
        "rule:architecture-invariants"
    )


@pytest.mark.offline
def test_prefer_inject_agent_skill_requires_evidence() -> None:
    assert (
        prefer_inject_entity_id("rule:orchestrator-core", cutover=True)
        == "agent_skill:orchestrator-core"
    )
    with pytest.raises(BootReadCutoverError):
        prefer_inject_entity_id("rule:architecture-invariants", cutover=True)
