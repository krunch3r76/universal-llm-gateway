"""Two-axis routing derivation — orchestration_mode × executor_style."""

from __future__ import annotations

from implement_admission.spec import (
    ExecutorStyle,
    OrchestrationMode,
    Routing,
    RoutingDerivation,
    SourceKind,
)


def derive_orchestration_mode(
    source_kind: str,
    *,
    multi_phase: bool = False,
    trips_todo_plan_threshold: bool = False,
    packet_shape: str = "single",
    ambiguous_bus: bool = False,
) -> str | None:
    """Axis 1 — return mode string or None when route must gate."""
    if source_kind == SourceKind.AGENT_BUS.value and ambiguous_bus:
        return None
    if source_kind == SourceKind.PLAN.value and multi_phase:
        return OrchestrationMode.COORDINATOR.value
    if source_kind == SourceKind.TODO.value and trips_todo_plan_threshold:
        return OrchestrationMode.COORDINATOR.value
    if source_kind == SourceKind.PACKET.value and packet_shape == "multi":
        return OrchestrationMode.COORDINATOR.value
    if source_kind in {
        SourceKind.PLAN_PHASE.value,
        SourceKind.TODO.value,
        SourceKind.PACKET.value,
        SourceKind.AGENT_BUS.value,
    }:
        return OrchestrationMode.SINGLE.value
    if source_kind == SourceKind.PLAN.value:
        return OrchestrationMode.SINGLE.value
    return OrchestrationMode.SINGLE.value


def derive_executor_style(
    *,
    has_complete_file_list: bool = False,
    has_dense_acs: bool = False,
    open_design: bool = False,
    dirty_tree_risk: bool = False,
    irreversible_gate: bool = False,
) -> tuple[str, bool]:
    """Axis 2 — return (executor_style, checkpoint_required)."""
    if dirty_tree_risk or irreversible_gate or open_design:
        return ExecutorStyle.REASONING.value, True
    if has_complete_file_list and has_dense_acs and not open_design:
        return ExecutorStyle.MECHANICAL.value, False
    return ExecutorStyle.REASONING.value, False


def derive_routing(
    source_kind: str,
    *,
    multi_phase: bool = False,
    trips_todo_plan_threshold: bool = False,
    packet_shape: str = "single",
    ambiguous_bus: bool = False,
    has_complete_file_list: bool = False,
    has_dense_acs: bool = False,
    open_design: bool = False,
    dirty_tree_risk: bool = False,
    irreversible_gate: bool = False,
    requested_execution_mode: str | None = None,
) -> Routing | None:
    """Compose both axes; return None when orchestration cannot be derived."""
    mode = derive_orchestration_mode(
        source_kind,
        multi_phase=multi_phase,
        trips_todo_plan_threshold=trips_todo_plan_threshold,
        packet_shape=packet_shape,
        ambiguous_bus=ambiguous_bus,
    )
    if mode is None:
        return None

    style, checkpoint = derive_executor_style(
        has_complete_file_list=has_complete_file_list,
        has_dense_acs=has_dense_acs,
        open_design=open_design,
        dirty_tree_risk=dirty_tree_risk,
        irreversible_gate=irreversible_gate,
    )

    mode_rule = _mode_rule(
        source_kind,
        multi_phase=multi_phase,
        trips_todo_plan_threshold=trips_todo_plan_threshold,
        packet_shape=packet_shape,
        ambiguous_bus=ambiguous_bus,
        mode=mode,
    )
    style_rule = _style_rule(
        has_complete_file_list=has_complete_file_list,
        has_dense_acs=has_dense_acs,
        open_design=open_design,
        dirty_tree_risk=dirty_tree_risk,
        irreversible_gate=irreversible_gate,
        style=style,
        checkpoint=checkpoint,
    )

    return Routing(
        orchestration_mode=OrchestrationMode(mode),
        executor_style=ExecutorStyle(style),
        checkpoint_required=checkpoint,
        derivation=RoutingDerivation(mode_rule=mode_rule, style_rule=style_rule),
        requested_execution_mode=requested_execution_mode,
    )


def _mode_rule(
    source_kind: str,
    *,
    multi_phase: bool,
    trips_todo_plan_threshold: bool,
    packet_shape: str,
    ambiguous_bus: bool,
    mode: str,
) -> str:
    if ambiguous_bus:
        return "agent-bus:* ambiguous — no route (gated)"
    if trips_todo_plan_threshold:
        return "todo tripping Todo→Plan threshold → coordinator"
    if source_kind == SourceKind.PLAN.value and multi_phase:
        return "plan multi-phase arc → coordinator"
    if packet_shape == "multi":
        return "packet with phase deck / parallel groups → coordinator"
    return f"single bounded {source_kind} → {mode}"


def _style_rule(
    *,
    has_complete_file_list: bool,
    has_dense_acs: bool,
    open_design: bool,
    dirty_tree_risk: bool,
    irreversible_gate: bool,
    style: str,
    checkpoint: bool,
) -> str:
    if dirty_tree_risk:
        return "code-modifying with dirty/shared-tree risk → reasoning + checkpoint"
    if irreversible_gate:
        return "legal/financial/irreversible gate → reasoning + checkpoint"
    if open_design:
        return "sparse/architectural, open substrate choice → reasoning"
    if has_complete_file_list and has_dense_acs:
        return "complete file list + dense ACs, no open design → mechanical"
    return f"default style derivation → {style}" + (" + checkpoint" if checkpoint else "")
