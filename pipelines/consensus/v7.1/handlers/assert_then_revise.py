"""
Deterministic assert-then-revise handler for citation enforcement.

Checks whether uncited sentences (from find_uncited_filter) still appear
verbatim in the artifact. If any remain, calls a reviser to delete them.
Retries up to max_retries times. Outputs the cleaned artifact.

Invariants:
    ∀ s ∈ uncited_sentences:
        s.strip() ∉ output.raw ∨ (reviser failed after max_retries)
    ∀ s ∈ artifact: s ∉ uncited_sentences ⟹ s ∈ output.raw
        (non-target content preserved)
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any, override

from systems.pipeline.core.execution.resolver import NamespaceResolver
from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput
from universal_logging import get_logger

if TYPE_CHECKING:
    from systems.pipeline.core.handlers.protocol import PipelineContext
    from systems.pipeline.core.schemas import StepConfig

logger = get_logger(__name__)


def _find_remaining(artifact: str, uncited: list[str]) -> list[str]:
    """Return uncited sentences still present verbatim in the artifact."""
    return [s for s in uncited if s.strip() in artifact]


class AssertThenReviseHandler(BaseHandler):
    """
    Deterministic assertion with targeted LLM revision.

    If uncited sentences remain in the artifact, call reviser to delete them.
    Retry up to max_retries. If assertions pass, output artifact unchanged.
    """

    step_type: str = "consensus_assert_then_revise_v7_1"

    @override
    async def execute(
        self,
        step: StepConfig,
        context: PipelineContext,
    ) -> StepOutput:
        start_time = time.time()
        resolver = NamespaceResolver(context)

        artifact: str = self._resolve_input(
            resolver, step, "artifact", step.handler_inputs
        )
        uncited: list[str] = (
            self._resolve_input(
                resolver, step, "uncited_sentences", step.handler_inputs
            )
            or []
        )
        max_retries: int = step.get_domain_field("max_retries") or 2

        remaining = _find_remaining(artifact, uncited)
        initial_remaining = remaining.copy()
        initial_count = len(initial_remaining)

        if not remaining:
            latency_ms = (time.time() - start_time) * 1000
            logger.info(
                "Step '%s': all assertions pass, no uncited sentences in artifact (%.1fms)",
                step.id,
                latency_ms,
            )
            return StepOutput(
                raw=artifact,
                json={
                    "passed": True,
                    "initial_count": 0,
                    "remaining_count": 0,
                    "attempts": 0,
                    "removed": [],
                },
                step_id=step.id,
                latency_ms=latency_ms,
            )

        # Resolve revise action config
        actions: dict[str, Any] = step.get_domain_field("actions") or {}
        revise_config = actions.get("revise")
        if not revise_config:
            raise ValueError(
                f"Step '{step.id}' missing 'revise' action in actions config"
            )

        prompt_ref: str = revise_config["prompt_ref"]
        model_ref: str = revise_config.get("model_ref") or step.get_domain_field("model_ref") or ""
        if not model_ref:
            raise ValueError(f"Step '{step.id}' missing model_ref for revise action")

        resolved_model = self._resolve_model_alias(model_ref, context)

        total_prompt_tokens = 0
        total_completion_tokens = 0
        model_call_count = 0

        for attempt in range(max_retries):
            targets_text = "\n".join(
                f'{i + 1}. "{s}"' for i, s in enumerate(remaining)
            )
            rendered = self._render_prompt(
                prompt_ref,
                {"artifact": artifact, "targets": targets_text},
                context,
            )

            result = await self._call_model(
                resolved_model,
                rendered.user_prompt,
                step,
                context,
                rendered.system_prompt,
                temperature=0.0,
                call_label=f"c4_revise_{attempt}",
                model_id_is_resolved=True,
            )

            total_prompt_tokens += result.prompt_tokens
            total_completion_tokens += result.completion_tokens
            model_call_count += 1

            artifact = result.content.strip()
            remaining = _find_remaining(artifact, uncited)

            logger.info(
                "Step '%s': attempt %d/%d — %d remaining (was %d)",
                step.id,
                attempt + 1,
                max_retries,
                len(remaining),
                initial_count,
            )

            if not remaining:
                break

        if remaining:
            logger.error(
                "Step '%s': %d uncited sentence(s) remain after %d revise attempts: %s",
                step.id,
                len(remaining),
                max_retries,
                [s[:80] for s in remaining],
            )

        latency_ms = (time.time() - start_time) * 1000
        passed = len(remaining) == 0

        return StepOutput(
            raw=artifact,
            json={
                "passed": passed,
                "initial_count": initial_count,
                "remaining_count": len(remaining),
                "attempts": model_call_count,
                "removed": [s[:80] for s in initial_remaining if s not in remaining],
            },
            step_id=step.id,
            latency_ms=latency_ms,
            prompt_tokens=total_prompt_tokens,
            completion_tokens=total_completion_tokens,
            model_call_count=model_call_count,
            model_id=resolved_model,
        )

    @override
    def validate(self, step: StepConfig) -> list[str]:
        errors: list[str] = []
        hi = step.handler_inputs or {}
        if "artifact" not in hi:
            errors.append(f"Step '{step.id}' missing 'artifact' in handler_inputs")
        if "uncited_sentences" not in hi:
            errors.append(
                f"Step '{step.id}' missing 'uncited_sentences' in handler_inputs"
            )
        actions = step.get_domain_field("actions") or {}
        if "revise" not in actions:
            errors.append(f"Step '{step.id}' missing 'revise' action in actions config")
        elif "prompt_ref" not in actions["revise"]:
            errors.append(
                f"Step '{step.id}' missing 'prompt_ref' in revise action config"
            )
        return errors
