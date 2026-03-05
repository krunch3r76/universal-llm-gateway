"""
SelectWinnerHandler — promote one step's output as the pipeline's final answer.

This handler exists because DAG outputs are keyed by step name, but many
pipelines finish with a synthetic "winner" step that re-surfaces an upstream
result without any further processing. Decoupled from BaseHandler intentionally:
it needs no model invocation, no prompt rendering, no token management.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from universal_logging import get_logger

from ..protocol import PipelineContext, StepOutput

if TYPE_CHECKING:
    from ..schemas import StepConfig

logger = get_logger(__name__)


class SelectWinnerHandler:
    """
    Handler for select_winner steps.

    Domain-agnostic - just selects output from a previous step.
    Returns StepOutput; does NOT write to context.
    """

    step_type = "select_winner"

    async def execute(
        self,
        step: StepConfig,
        context: PipelineContext,
    ) -> StepOutput:
        """Select output from a previous step."""
        source_step = step.from_

        if not source_step:
            raise ValueError(f"select_winner '{step.id}' missing 'from' field")

        source_output = context.get_output(source_step)

        if source_output is None:
            logger.error(
                "select_winner '%s': source step '%s' not found in outputs — "
                "expected upstream step to write output but none present. "
                "Available: %s",
                step.id,
                source_step,
                list(context.outputs.keys()),
            )
            return StepOutput(raw="")

        logger.info(f"select_winner '{step.id}': selected from '{source_step}'")

        return StepOutput(
            raw=source_output.text,
            model_id=source_output.model_id,
        )

    def validate(self, step: StepConfig) -> list[str]:
        """Return configuration errors (called at load time, not at execution)."""
        errors = []
        if not step.from_:
            errors.append(f"select_winner '{step.id}' missing 'from' field")
        return errors
