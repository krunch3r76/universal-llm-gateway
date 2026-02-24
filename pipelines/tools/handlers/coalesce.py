"""
Coalesce step handler.

Selects the first non-empty input from an ordered set of sources.
Designed for conditional expansion patterns where earlier steps may be SKIPPED.

Invariants:
- ∀ execute(): iterates handler_inputs in insertion order (Python 3.7+ dict ordering)
- ∀ SKIPPED step input: resolved raw == "" → treated as empty, skipped to next source
- ∀ all-empty: returns StepOutput(raw="", json={"_coalesced": True, "_all_empty": True})
- Requires ≥2 handler_inputs (validated at load time)
"""

from __future__ import annotations

from typing import TYPE_CHECKING, override

from systems.pipeline.core.execution.resolver import NamespaceResolver, traverse_path
from systems.pipeline.core.handlers.protocol import AbstractStepHandler, StepOutput
from universal_logging import get_logger

if TYPE_CHECKING:
    from systems.pipeline.core.handlers.protocol import PipelineContext
    from systems.pipeline.core.schemas import StepConfig

logger = get_logger(__name__)


class CoalesceHandler(AbstractStepHandler):
    """
    Select the first non-empty value from an ordered set of handler_inputs.

    Use when a step may be SKIPPED (condition evaluated False) and a fallback
    is needed. Each source is resolved in YAML declaration order; the first
    non-empty string wins.

    YAML usage:
        - name: git_context
          type: coalesce_v1
          handler_inputs:
            primary: "git_expanded.raw"
            fallback: "git_recent.raw"

    Invariant: ∀ SKIPPED step S: S.raw == "" ⟹ coalesce skips S → next source
    """

    step_type: str = "coalesce_v1"

    @override
    async def execute(
        self,
        step: StepConfig,
        context: PipelineContext,
    ) -> StepOutput:
        resolver = NamespaceResolver(context)

        for field_name, binding in step.handler_inputs.items():
            root = resolver.resolve(binding)
            value = traverse_path(
                root,
                binding.field_path,
                step_name=step.id,
                field_name=field_name,
                binding_repr=str(binding),
            )

            if isinstance(value, str) and value.strip():
                logger.debug(
                    f"Coalesce '{step.id}': selected '{field_name}' "
                    f"({len(value)} chars)"
                )
                return StepOutput(
                    raw=value,
                    json={"_coalesced_from": field_name},
                )

        logger.debug(f"Coalesce '{step.id}': all sources empty")
        return StepOutput(raw="", json={"_coalesced": True, "_all_empty": True})

    @override
    def validate(self, step: StepConfig) -> list[str]:
        errors = []
        if len(step.handler_inputs) < 2:
            errors.append(
                f"Step '{step.id}' (coalesce_v1) requires ≥2 handler_inputs, "
                f"got {len(step.handler_inputs)}"
            )
        return errors
