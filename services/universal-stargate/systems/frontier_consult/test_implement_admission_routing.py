"""Tests for two-axis routing derivation."""

from __future__ import annotations

from implement_admission.routing import derive_executor_style, derive_orchestration_mode, derive_routing
from implement_admission.spec import ExecutorStyle, OrchestrationMode, SourceKind


def test_plan_multi_phase_coordinator() -> None:
    mode = derive_orchestration_mode(SourceKind.PLAN.value, multi_phase=True)
    assert mode == OrchestrationMode.COORDINATOR.value


def test_todo_bounded_single() -> None:
    mode = derive_orchestration_mode(SourceKind.TODO.value)
    assert mode == OrchestrationMode.SINGLE.value


def test_ambiguous_bus_no_mode() -> None:
    assert derive_orchestration_mode(SourceKind.AGENT_BUS.value, ambiguous_bus=True) is None


def test_mechanical_style_dense_acs() -> None:
    style, checkpoint = derive_executor_style(
        has_complete_file_list=True,
        has_dense_acs=True,
    )
    assert style == ExecutorStyle.MECHANICAL.value
    assert checkpoint is False


def test_reasoning_dirty_tree() -> None:
    style, checkpoint = derive_executor_style(dirty_tree_risk=True)
    assert style == ExecutorStyle.REASONING.value
    assert checkpoint is True


def test_derive_routing_populates_derivation_strings() -> None:
    routing = derive_routing(
        SourceKind.TODO.value,
        has_complete_file_list=True,
        has_dense_acs=True,
    )
    assert routing is not None
    assert routing.derivation.mode_rule
    assert routing.derivation.style_rule
