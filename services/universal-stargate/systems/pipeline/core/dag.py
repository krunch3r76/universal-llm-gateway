"""
DAG (Directed Acyclic Graph) builder for pipeline steps.

Provides:
- Dependency graph construction
- Cycle detection
- Topological sorting

Invariants:
- ∀ step: step.depends_on ⊆ {s.id | s ∈ pipeline.steps}
- ¬∃ cycle in dependency graph
- ∀ step: all dependencies complete before step executes

Note: DAGExecutor has been moved to execution/executor.py
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum, auto
from typing import TYPE_CHECKING

from universal_logging import get_logger

if TYPE_CHECKING:
    from .handlers.protocol import StepOutput
    from .schemas import StepConfig

logger = get_logger(__name__)


class StepState(StrEnum):
    """Execution state of a step."""

    PENDING = auto()
    READY = auto()
    RUNNING = auto()
    COMPLETED = auto()
    SKIPPED = auto()
    FAILED = auto()


@dataclass
class StepNode:
    """Node in the execution DAG."""

    step: StepConfig
    state: StepState = StepState.PENDING
    dependencies: set[str] = field(default_factory=set)
    dependents: set[str] = field(default_factory=set)
    output: StepOutput | None = None
    error: Exception | None = None


class DAGBuilder:
    """
    Build execution DAG from pipeline steps.

    Validates:
    - No dangling dependency references
    - No cycles
    - All inputs are valid step references

    NO IMPLICIT DEPENDENCIES: All dependencies must be explicitly
    declared via depends_on field. There is no inference.
    """

    def __init__(self, steps: list[StepConfig], validate_only: bool = False):
        self.steps = steps
        self.nodes: dict[str, StepNode] = {}
        self.validate_only = validate_only

    def build(self) -> dict[str, StepNode]:
        """
        Build and validate the DAG.

        Returns:
            Dict of step_id -> StepNode

        Raises:
            ValueError: If DAG is invalid (cycles, dangling refs)
        """
        # Create nodes with computed dependencies (v5 schema)
        for step in self.steps:
            self.nodes[step.id] = StepNode(
                step=step,
                dependencies=set(step.computed_depends_on),
            )

        # Validate dependency references and build reverse edges
        for step_id, node in self.nodes.items():
            for dep_id in node.dependencies:
                if dep_id not in self.nodes:
                    raise ValueError(
                        f"Step '{step_id}' depends on unknown step '{dep_id}'. "
                        f"Available steps: {list(self.nodes.keys())}"
                    )
                self.nodes[dep_id].dependents.add(step_id)

        # Validate inputs references (for judge steps)
        self._validate_inputs_refs()

        # Detect cycles
        self._detect_cycles()

        # Mark initially ready steps
        self._mark_ready_steps()

        ready_count = sum(1 for n in self.nodes.values() if not n.dependencies)
        if not self.validate_only:
            logger.info(f"DAG built: {len(self.nodes)} nodes, {ready_count} ready")

        return self.nodes

    def _validate_inputs_refs(self) -> None:
        """Validate that step inputs reference valid steps."""
        for step_id, node in self.nodes.items():
            step = node.step
            if step.inputs:
                for input_id in step.inputs:
                    if input_id not in self.nodes:
                        raise ValueError(
                            f"Step '{step_id}' has input '{input_id}' "
                            f"which is not a valid step. "
                            f"Available steps: {list(self.nodes.keys())}"
                        )

    def _detect_cycles(self) -> None:
        """
        Detect cycles using DFS with colors.

        Colors: WHITE=unvisited, GRAY=in-progress, BLACK=done
        Cycle exists if we visit a GRAY node.

        Raises:
            ValueError: If cycle detected
        """
        white, gray, black = 0, 1, 2
        colors = {step_id: white for step_id in self.nodes}

        def dfs(step_id: str, path: list[str]) -> None:
            if colors[step_id] == gray:
                # Found cycle - construct path
                cycle_start = path.index(step_id)
                cycle = path[cycle_start:] + [step_id]
                raise ValueError(f"Cycle detected in pipeline: {' → '.join(cycle)}")
            if colors[step_id] == black:
                return

            colors[step_id] = gray
            path.append(step_id)

            for dep_id in self.nodes[step_id].dependents:
                dfs(dep_id, path)

            _ = path.pop()
            colors[step_id] = black

        for step_id in self.nodes:
            if colors[step_id] == white:
                dfs(step_id, [])

    def _mark_ready_steps(self) -> None:
        """Mark steps with no dependencies as READY."""
        for node in self.nodes.values():
            if not node.dependencies:
                node.state = StepState.READY


class PipelineExecutionError(Exception):
    """Raised when pipeline execution fails."""

    pass


class ResponseTruncatedError(PipelineExecutionError):
    """Raised when a model response is truncated due to max_tokens limit.

    Fail-fast: prevents truncated output from propagating to downstream
    steps where it causes cryptic failures (e.g., malformed JSON).
    """

    def __init__(
        self,
        step_id: str,
        completion_tokens: int,
        max_tokens: int | None,
        response_preview: str,
    ) -> None:
        self.step_id = step_id
        self.completion_tokens = completion_tokens
        self.max_tokens = max_tokens
        self.response_preview = response_preview

        token_info = (
            f"{completion_tokens}/{max_tokens} tokens used"
            if max_tokens is not None
            else f"{completion_tokens} tokens used"
        )
        super().__init__(
            f"Step '{step_id}' response truncated: "
            f"hit max_tokens limit ({token_info}). "
            f"Increase max_tokens or simplify prompt. "
            f"Partial response: {response_preview[:200]}"
        )
