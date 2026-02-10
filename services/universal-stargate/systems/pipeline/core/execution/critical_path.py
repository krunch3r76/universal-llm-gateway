"""
Critical path calculation for pipeline DAGs.

Architecture Decision (ADR-2): STATIC ANALYSIS
===============================================
This module uses static analysis at batch creation time, NOT dynamic
recalculation per iteration. See ADR-2 in phase6.2.md for full rationale.

Key points:
- DAG topology is immutable → critical path doesn't change
- O(V+E) is cheap → sub-millisecond for typical pipelines (10-50 steps)
- Actual step durations don't help model loading → decision made BEFORE execution
- Simplicity wins → no cache invalidation, no event wiring, deterministic

Domain: Pipeline
Algorithm: O(V + E) where V = steps, E = dependencies

Critical Path Definition:
    The sequence of dependent steps that determines the minimum total
    execution time. Steps on the critical path have zero "slack" -
    any delay directly increases total pipeline duration.

Complexity Guarantees:
    - calculate_critical_path: O(V + E) - two passes over DAG
    - calculate_step_depths: O(V + E) - single pass over DAG
    - find_parallel_siblings: O(V × avg_deps) - sibling discovery
    - _topological_sort: O(V + E) - Kahn's algorithm

Memory Usage:
    - O(V) for earliest/latest start/finish dictionaries
    - O(V) for depth mapping
    - O(V × avg_siblings) for sibling mapping
    - All transient (garbage collected after batch creation)

Why NOT Dynamic Recalculation:
    Dynamic recalculation would require:
    1. Event subscription for STEP_COMPLETED events
    2. Cache invalidation logic with dirty flags
    3. Duration tracking across batch boundaries
    4. Cross-cutting event dependencies

    For minimal benefit:
    - Early steps' durations don't help prioritize (already done)
    - Late steps still use estimates (no observed data yet)
    - Model load decisions happen BEFORE step execution

    The topology—not timing—determines what's critical.
"""

from __future__ import annotations

from collections import deque
from typing import TYPE_CHECKING

from universal_logging import get_logger

if TYPE_CHECKING:
    from ..dag import StepNode

logger = get_logger(__name__)


# =============================================================================
# PUBLIC API - Static Analysis Functions
# =============================================================================


def calculate_critical_path(
    nodes: dict[str, StepNode],
    step_durations: dict[str, float] | None = None,
) -> list[str]:
    """
    Calculate critical path step IDs using CPM (Critical Path Method).

    This is STATIC ANALYSIS - computed fresh per batch, not cached.
    See module docstring for ADR-2 rationale.

    Algorithm: O(V + E)
    ===================
    1. Topological sort: O(V + E) using Kahn's algorithm
    2. Forward pass: O(V + E) - compute earliest finish time for each step
    3. Backward pass: O(V + E) - compute latest finish time for each step
    4. Critical path = steps where earliest == latest (zero slack)

    Args:
        nodes: Dict mapping step_id → StepNode
        step_durations: Optional dict mapping step_id → estimated duration.
                       If not provided, assumes uniform duration (1.0).

                       Note: Uniform duration is appropriate because:
                       - We prioritize by TOPOLOGY, not actual timing
                       - Config-based hints per step type are a future enhancement

    Returns:
        List of step IDs on the critical path (in execution order)

    Complexity:
        Time: O(V + E) where V = |nodes|, E = total dependencies
        Space: O(V) for timing dictionaries

    Example:
        Given DAG: A → B → C
                   A → D

        With uniform durations:
        - A: earliest=0, latest=0 (critical)
        - B: earliest=1, latest=1 (critical)
        - C: earliest=2, latest=2 (critical)
        - D: earliest=1, latest=2 (slack=1, NOT critical)

        Critical path: [A, B, C]
    """
    if not nodes:
        return []

    # Default to uniform duration if not provided
    # Uniform duration prioritizes by topology, not timing estimates
    durations = step_durations or {step_id: 1.0 for step_id in nodes}

    # Step 1: Topological sort - O(V + E)
    sorted_ids = _topological_sort(nodes)

    if not sorted_ids:
        logger.warning("Topological sort returned empty - possible cycle in DAG")
        return []

    # Step 2: Forward pass - earliest finish times - O(V + E)
    earliest_finish: dict[str, float] = {}
    earliest_start: dict[str, float] = {}

    for step_id in sorted_ids:
        node = nodes[step_id]
        # Earliest start = max(earliest finish of all dependencies)
        if not node.dependencies:
            earliest_start[step_id] = 0.0
        else:
            earliest_start[step_id] = max(
                earliest_finish.get(dep_id, 0.0) for dep_id in node.dependencies
            )
        earliest_finish[step_id] = earliest_start[step_id] + durations.get(step_id, 1.0)

    # Step 3: Backward pass - latest finish times - O(V + E)
    total_duration = max(earliest_finish.values()) if earliest_finish else 0.0
    latest_finish: dict[str, float] = {}
    latest_start: dict[str, float] = {}

    for step_id in reversed(sorted_ids):
        node = nodes[step_id]
        # Latest finish = min(latest start of all dependents)
        if not node.dependents:
            latest_finish[step_id] = total_duration
        else:
            latest_finish[step_id] = min(
                latest_start.get(dep_id, total_duration) for dep_id in node.dependents
            )
        latest_start[step_id] = latest_finish[step_id] - durations.get(step_id, 1.0)

    # Step 4: Find critical path (zero slack) - O(V)
    epsilon = 1e-9  # Floating point tolerance
    critical_path = [
        step_id
        for step_id in sorted_ids
        if abs(earliest_start[step_id] - latest_start[step_id]) < epsilon
    ]

    logger.debug(
        f"Critical path: {len(critical_path)}/{len(nodes)} steps, "
        f"duration: {total_duration:.2f}"
    )

    return critical_path


def calculate_step_depths(nodes: dict[str, StepNode]) -> dict[str, int]:
    """
    Calculate depth of each step in the DAG.

    This is STATIC ANALYSIS - computed fresh per batch.

    Depth = longest path from any root to this step.
    Roots have depth 0.

    Used for: Prioritizing shallow steps (execute early in pipeline).

    Args:
        nodes: Dict mapping step_id → StepNode

    Returns:
        Dict mapping step_id → depth (0-indexed)

    Complexity:
        Time: O(V + E) - single topological pass
        Space: O(V) for depth mapping

    Example:
        Given DAG: A → B → C
                   A → D → E

        Depths: {A: 0, B: 1, D: 1, C: 2, E: 2}
    """
    if not nodes:
        return {}

    depths: dict[str, int] = {}
    sorted_ids = _topological_sort(nodes)

    for step_id in sorted_ids:
        node = nodes[step_id]
        if not node.dependencies:
            depths[step_id] = 0
        else:
            depths[step_id] = (
                max(depths.get(dep_id, 0) for dep_id in node.dependencies) + 1
            )

    return depths


def find_parallel_siblings(nodes: dict[str, StepNode]) -> dict[str, list[str]]:
    """
    Find parallel siblings for each step.

    This is STATIC ANALYSIS - computed fresh per batch.

    Siblings = steps that share at least one common dependency and
    could potentially execute in parallel once their deps complete.

    Used for: Scoring parallel enablement (loading model X enables siblings).

    Args:
        nodes: Dict mapping step_id → StepNode

    Returns:
        Dict mapping step_id → list of sibling step IDs (sorted for determinism)

    Complexity:
        Time: O(V × avg_deps) - iterate dependencies per node
        Space: O(V × avg_siblings) for sibling mapping

    Example:
        Given DAG: A → B
                   A → C
                   A → D

        Siblings: {B: [C, D], C: [B, D], D: [B, C]}

        Loading model for B enables C and D to potentially run in parallel.
    """
    if not nodes:
        return {}

    # Build reverse mapping: dependency → dependents - O(E)
    dep_to_dependents: dict[str, set[str]] = {}
    for step_id, node in nodes.items():
        for dep_id in node.dependencies:
            if dep_id not in dep_to_dependents:
                dep_to_dependents[dep_id] = set()
            dep_to_dependents[dep_id].add(step_id)

    # For each step, find siblings (other steps with same parent) - O(V × avg_deps)
    siblings: dict[str, list[str]] = {}
    for step_id, node in nodes.items():
        sibling_set: set[str] = set()
        for dep_id in node.dependencies:
            sibling_set.update(dep_to_dependents.get(dep_id, set()))
        sibling_set.discard(step_id)  # Remove self
        siblings[step_id] = sorted(sibling_set)  # Sort for deterministic output

    return siblings


# =============================================================================
# INTERNAL HELPERS
# =============================================================================


def _topological_sort(nodes: dict[str, StepNode]) -> list[str]:
    """
    Topological sort using Kahn's algorithm.

    Uses deque for O(1) popleft instead of list.pop(0) which is O(n).

    Returns:
        Step IDs in dependency order (dependencies before dependents).
        Empty list if cycle detected.

    Complexity:
        Time: O(V + E)
        Space: O(V) for in-degree map and queue
    """
    # Calculate in-degree for each node - O(E)
    in_degree: dict[str, int] = {step_id: 0 for step_id in nodes}
    for node in nodes.values():
        for dep_id in node.dependents:
            if dep_id in in_degree:
                in_degree[dep_id] += 1

    # Start with nodes that have no dependencies (in-degree 0)
    queue: deque[str] = deque(
        step_id for step_id, degree in in_degree.items() if degree == 0
    )
    result: list[str] = []

    # Process queue - O(V + E) total
    while queue:
        step_id = queue.popleft()  # O(1) with deque
        result.append(step_id)

        node = nodes[step_id]
        for dependent_id in node.dependents:
            if dependent_id in in_degree:
                in_degree[dependent_id] -= 1
                if in_degree[dependent_id] == 0:
                    queue.append(dependent_id)

    # Check for cycles (not all nodes processed)
    if len(result) != len(nodes):
        logger.error(
            f"Cycle detected in DAG: processed {len(result)}/{len(nodes)} nodes"
        )
        return []

    return result
