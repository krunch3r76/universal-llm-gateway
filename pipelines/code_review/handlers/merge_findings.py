"""
Deterministic merge handler for the code-review pipeline.

Reconciles validated findings: drops rejected, applies severity adjustments,
deduplicates by (category, target), sorts by severity.

No LLM call — pure data transformation.

Invariants:
    ∀ finding ∈ output: finding.validator_status ≠ "rejected"
    ∀ (category, target) pair: |findings with pair| ≤ 1
    output sorted by severity: critical < warning < suggestion
"""

from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING, Any, override

from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput
from universal_logging import get_logger

if TYPE_CHECKING:
    from systems.pipeline.core.handlers.protocol import PipelineContext
    from systems.pipeline.core.schemas import StepConfig

logger = get_logger(__name__)

_SEVERITY_ORDER: dict[str, int] = {"critical": 0, "warning": 1, "suggestion": 2}


def _merge_validated_findings(
    validated_findings: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    """Filter rejected, apply severity, deduplicate, sort.

    Returns (merged_findings, rejected_count). Findings are returned in final
    output schema (severity from validated_severity, original_severity removed).
    Dicts are copied before mutation — caller's list is never modified.
    """
    rejected_count = 0
    accepted: list[dict[str, Any]] = []
    for f in validated_findings:
        if f.get("validator_status") == "rejected":
            rejected_count += 1
        else:
            copy = dict(f)
            if "validated_severity" in copy:
                copy["severity"] = copy.pop("validated_severity")
            copy.pop("original_severity", None)
            accepted.append(copy)

    seen: dict[tuple[str, str], int] = {}
    deduped: list[dict[str, Any]] = []
    for finding in accepted:
        key = (finding.get("category", ""), finding.get("target", ""))
        if key not in seen:
            seen[key] = len(deduped)
            deduped.append(finding)
        else:
            existing_idx = seen[key]
            existing_sev = _SEVERITY_ORDER.get(
                deduped[existing_idx].get("severity", "suggestion"), 2
            )
            new_sev = _SEVERITY_ORDER.get(finding.get("severity", "suggestion"), 2)
            if new_sev < existing_sev:
                deduped[existing_idx] = finding

    deduped.sort(key=lambda f: _SEVERITY_ORDER.get(f.get("severity", "suggestion"), 2))
    return deduped, rejected_count


class MergeFindingsHandler(BaseHandler):
    """Reconcile validated code-review findings into final structured output."""

    step_type: str = "code_review_merge_findings"

    @override
    async def execute(
        self,
        step: StepConfig,
        context: PipelineContext,
    ) -> StepOutput:
        """Merge validated findings: filter rejected, deduplicate, sort by severity."""
        start_time = time.time()

        inputs = step.handler_inputs or {}
        validated_output_binding = inputs.get("validated_output")
        source_step = "validate"
        if (
            validated_output_binding is not None
            and validated_output_binding.namespace == "step"
            and validated_output_binding.step_name
        ):
            source_step = validated_output_binding.step_name

        validate_output = context.get_output(source_step)
        if not validate_output:
            empty_result: dict[str, Any] = {
                "findings": [],
                "clean_files": [],
                "event_coverage": [],
            }
            return StepOutput(
                raw=json.dumps(empty_result, indent=2),
                json=empty_result,
                step_id=step.id,
                latency_ms=0.0,
            )

        try:
            validated: dict[str, Any] = json.loads(validate_output.raw)
        except json.JSONDecodeError as e:
            logger.error(
                "Step '%s': failed to parse validate output as JSON: %s",
                step.id,
                e,
            )
            return StepOutput(
                raw=validate_output.raw,
                step_id=step.id,
                error=f"JSON parse error on validate output: {e}",
            )

        validated_findings: list[dict[str, Any]] = validated.get(
            "validated_findings", []
        )
        original_count = len(validated_findings)
        merged, rejected_count = _merge_validated_findings(validated_findings)
        dedup_removed = (original_count - rejected_count) - len(merged)

        result: dict[str, Any] = {
            "findings": merged,
            "clean_files": validated.get("clean_files", []),
            "event_coverage": validated.get("event_coverage", []),
        }

        latency_ms = (time.time() - start_time) * 1000

        logger.info(
            "Step '%s': merged %d findings → %d (rejected=%d, deduped=%d) (%.0fms)",
            step.id,
            original_count,
            len(merged),
            rejected_count,
            dedup_removed,
            latency_ms,
        )

        return StepOutput(
            raw=json.dumps(result, indent=2),
            json=result,
            step_id=step.id,
            latency_ms=latency_ms,
        )

    @override
    def validate(self, step: StepConfig) -> list[str]:
        """Validate merge step configuration."""
        errors: list[str] = []
        inputs = step.handler_inputs or {}
        if "validated_output" not in inputs:
            errors.append(
                f"Step '{step.id}': code_review_merge_findings requires "
                "'validated_output' in handler_inputs"
            )
        return errors
