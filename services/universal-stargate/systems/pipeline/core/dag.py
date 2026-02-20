"""
DAG (Directed Acyclic Graph) builder for pipeline steps.

Provides:
- Dependency graph construction
- Sub-pipeline expansion (``type: sub_pipeline`` → namespaced flat steps)
- Cycle detection
- Topological sorting

Invariants:
- ∀ step: step.depends_on ⊆ {s.id | s ∈ pipeline.steps}
- ¬∃ cycle in dependency graph
- ∀ step: all dependencies complete before step executes
- After expansion, the DAG is indistinguishable from a fully-inline pipeline

Note: DAGExecutor has been moved to execution/executor.py
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum, auto
from typing import TYPE_CHECKING, Any

from universal_logging import get_logger

if TYPE_CHECKING:
    from .handlers.protocol import StepOutput
    from .schemas import InputBinding, StepConfig, SubPipelineSpec

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
        self.output_aliases: dict[str, str] = {}
        self.validate_only = validate_only

    def build(self) -> dict[str, StepNode]:
        """
        Build and validate the DAG.

        Pre-pass: expand ``sub_pipeline`` steps into namespaced flat steps.
        After expansion, the DAG contains only concrete handler steps.

        Sets ``self.output_aliases``: maps each sub-pipeline parent step name
        to its resolved output step ID.  Callers must use this to resolve
        ``pipeline.output`` when it names a sub-pipeline step.

        Returns:
            Dict of step_id -> StepNode

        Raises:
            ValueError: If DAG is invalid (cycles, dangling refs)
        """
        self.steps, self.output_aliases = _expand_all_sub_pipelines(self.steps)

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


# ---------------------------------------------------------------------------
# Sub-pipeline expansion
# ---------------------------------------------------------------------------


SUB_PIPELINE_SEP = "__"
"""Separator for namespaced sub-pipeline step IDs.

Uses ``__`` instead of ``.`` because ``InputBinding.parse()`` splits on
the first dot to extract the step name. Dots inside step names would
break binding resolution.
"""


def _expand_all_sub_pipelines(
    steps: list[StepConfig],
) -> tuple[list[StepConfig], dict[str, str]]:
    """Replace ``sub_pipeline`` steps with namespaced flat steps.

    For each step with ``type == "sub_pipeline"``, the attached
    ``SubPipelineSpec`` (loaded by the loader) is expanded inline.
    Downstream bindings that reference the parent step name are rewritten
    to point at the sub-pipeline's output step.

    Returns:
        (expanded_steps, output_aliases) where output_aliases maps each
        sub-pipeline parent name to its resolved output step ID.
        Callers must use output_aliases to resolve ``pipeline.output``
        references that name a sub-pipeline step.
    """
    has_sub = any(s.type == "sub_pipeline" for s in steps)
    if not has_sub:
        return steps, {}

    output_aliases: dict[str, str] = {}
    expanded: list[StepConfig] = []

    for step in steps:
        if step.type != "sub_pipeline":
            expanded.append(step)
            continue

        sub_spec: SubPipelineSpec | None = step.get_domain_field("_sub_pipeline_spec")
        if sub_spec is None:
            raise ValueError(
                f"Step '{step.id}' has type 'sub_pipeline' but no loaded spec. "
                f"Ensure pipeline_ref is set and the loader resolved it."
            )

        parent_inputs = step.handler_inputs
        prefix = step.id
        output_step_name = f"{prefix}{SUB_PIPELINE_SEP}{sub_spec.output}"
        output_aliases[prefix] = output_step_name

        for sub_step in sub_spec.steps:
            namespaced = _namespace_step(sub_step, prefix, parent_inputs, sub_spec)
            expanded.append(namespaced)

        logger.info(
            f"Expanded sub-pipeline '{sub_spec.id}' into "
            f"{len(sub_spec.steps)} steps under prefix '{prefix}'"
        )

    if output_aliases:
        for step in expanded:
            _rewrite_aliases(step, output_aliases)

    return expanded, output_aliases


def _namespace_step(
    sub_step: StepConfig,
    prefix: str,
    parent_inputs: dict[str, InputBinding],
    sub_spec: SubPipelineSpec,
) -> StepConfig:
    """Create a namespaced copy of a sub-pipeline step.

    - Step name prefixed: ``decompose`` → ``verify_link0__decompose``
    - Internal step references prefixed
    - ``inputs.*`` bindings replaced with parent's handler_inputs
    - ``config.*_step`` references prefixed
    """
    from .schemas import InputBinding, StepConfig

    data: dict[str, Any] = sub_step.model_dump(by_alias=True, exclude_none=True)
    # model_dump(by_alias=True) emits "id" (alias for `name`). Overwrite the alias
    # key directly so Pydantic does not see a conflict between alias ("id") and field
    # name ("name") — Pydantic v2 with populate_by_name=True resolves ambiguity in
    # favour of the alias, which would silently keep the un-namespaced step name.
    data["id"] = f"{prefix}{SUB_PIPELINE_SEP}{sub_step.name}"
    # Opt out of exclusive DAG model locking: inference server manages concurrency.
    # Sub-pipeline steps share model_refs across chains; serialising at DAG level
    # would collapse all three verify chains into a sequential queue.
    data["_sub_pipeline_step"] = True

    new_inputs: dict[str, Any] = {}
    for key, binding in sub_step.handler_inputs.items():
        if binding.namespace == "step" and binding.step_name == "inputs":
            input_name = binding.field_path.split(".")[0]
            if input_name in parent_inputs:
                new_inputs[key] = parent_inputs[input_name]
            else:
                raise ValueError(
                    f"Sub-step '{sub_step.name}' references input '{input_name}' "
                    f"not provided by parent. "
                    f"Declared inputs: {sub_spec.inputs}, "
                    f"provided: {list(parent_inputs.keys())}"
                )
        elif binding.namespace == "step" and binding.step_name:
            new_inputs[key] = InputBinding(
                namespace="step",
                step_name=f"{prefix}{SUB_PIPELINE_SEP}{binding.step_name}",
                field_path=binding.field_path,
            )
        else:
            new_inputs[key] = binding
    data["handler_inputs"] = new_inputs

    if sub_step.model_extra:
        extra = dict(sub_step.model_extra)
        for ekey, eval_ in extra.items():
            if ekey.endswith("_step") and isinstance(eval_, str):
                extra[ekey] = f"{prefix}{SUB_PIPELINE_SEP}{eval_}"
            elif ekey == "config" and isinstance(eval_, dict):
                cfg = dict(eval_)
                for ck, cv in cfg.items():
                    if ck.endswith("_step") and isinstance(cv, str):
                        cfg[ck] = f"{prefix}{SUB_PIPELINE_SEP}{cv}"
                extra["config"] = cfg
        for ekey, eval_ in extra.items():
            if ekey not in data:
                data[ekey] = eval_

    return StepConfig(**data)


def _rewrite_aliases(
    step: StepConfig,
    aliases: dict[str, str],
) -> None:
    """Rewrite handler_input bindings that reference aliased parent step names."""
    from .schemas import InputBinding

    for key, binding in list(step.handler_inputs.items()):
        if binding.namespace == "step" and binding.step_name in aliases:
            step.handler_inputs[key] = InputBinding(
                namespace="step",
                step_name=aliases[binding.step_name],
                field_path=binding.field_path,
            )


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
