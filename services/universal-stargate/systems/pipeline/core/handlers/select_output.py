"""
Select-output handler: picks the first non-skipped result from candidate steps.

Reusable step type for pipelines with conditional branches where exactly
one of N candidate steps produces the real output.

Invariant: ∀ candidate ∈ candidates: candidate is a step name in the DAG
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from universal_logging import get_logger

from .protocol import StepOutput
from .registry import register_handler

if TYPE_CHECKING:
    from ..step_config import StepConfig
    from .protocol import PipelineContext

logger = get_logger(__name__)


@register_handler
class SelectOutputHandler:
    """
    Pick the first non-skipped output from an ordered list of candidate steps.

    YAML usage:
        - name: result
          type: select_output
          candidates: [execute_split, execute_refactor, plan_split]

    The candidates list is priority-ordered: the first candidate whose output
    exists and was not skipped wins. This allows fallback chains where the
    last candidate provides a "why it failed" explanation.
    """

    step_type = "select_output"
    dependency_fields: ClassVar[tuple[str, ...]] = ("candidates",)

    async def execute(
        self,
        step: StepConfig,
        context: PipelineContext,
    ) -> StepOutput:
        candidates: list[str] = step.get_domain_field("candidates", [])

        for name in candidates:
            output = context.get_output(name)
            if output is None:
                continue
            if isinstance(output.json, dict) and output.json.get("_skipped"):
                continue

            logger.info(
                "select_output '%s': selected from candidate '%s'",
                step.id,
                name,
            )
            return StepOutput(
                raw=output.raw,
                json=output.json,
                model_id=output.model_id,
            )

        available = list(context.outputs.keys())
        logger.warning(
            "select_output '%s': no candidate produced output. "
            "Candidates: %s, available: %s",
            step.id,
            candidates,
            available,
        )
        from ..dag import PipelineExecutionError

        raise PipelineExecutionError(
            f"Step '{step.id}': select_output exhausted all candidates. "
            f"None produced output. Candidates: {candidates}, available: {available}"
        )

    def validate(self, step: StepConfig) -> list[str]:
        """Validate the step configuration for SelectOutputHandler."""
        candidates = step.get_domain_field("candidates")
        if not candidates or not isinstance(candidates, list):
            return [
                f"Step '{step.id}': select_output requires a non-empty "
                f"'candidates' list of step names"
            ]
        return []
