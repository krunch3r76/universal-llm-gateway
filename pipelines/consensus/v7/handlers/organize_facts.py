"""Organize verified facts into a JSON outline and emit quality metrics."""

from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING, Any, cast, override

from systems.pipeline.core.execution.resolver import NamespaceResolver
from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput
from universal_logging import get_logger

from .shared._chain_utils import strip_json_fences

if TYPE_CHECKING:
    from systems.pipeline.core.handlers.protocol import PipelineContext
    from systems.pipeline.core.schemas import StepConfig

logger = get_logger(__name__)


def _extract_fact_texts(value: Any) -> list[str]:
    """Extract fact text strings from common verified-facts input shapes."""
    if isinstance(value, list) and value:
        if isinstance(value[0], dict):
            return [str(item.get("text", "")).strip() for item in value if item.get("text")]
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [line.strip() for line in value.splitlines() if line.strip()]
    return []


def _format_numbered_facts(facts: list[str]) -> str:
    """Render facts as numbered lines for prompt interpolation."""
    return "\n".join(f"[{index}] {fact}" for index, fact in enumerate(facts, 1))


def _collect_assigned_indices(sections: Any) -> list[int]:
    """Collect assigned fact indices from parsed outline sections."""
    if not isinstance(sections, list):
        return []
    assigned: list[int] = []
    for section in sections:
        if not isinstance(section, dict):
            continue
        indices = section.get("fact_indices")
        if not isinstance(indices, list):
            continue
        assigned.extend(i for i in indices if isinstance(i, int) and i > 0)
    return assigned


class OrganizeFactsHandler(BaseHandler):
    """Render organize prompt, call model, and emit organize outline metrics."""

    step_type: str = "consensus_organize_facts_v7"

    @override
    async def execute(self, step: StepConfig, context: PipelineContext) -> StepOutput:
        start_time = time.time()
        resolver = NamespaceResolver(cast(Any, context))
        handler_inputs = step.handler_inputs or {}

        resolved_inputs: dict[str, Any] = {
            name: self._resolve_input(resolver, step, name, handler_inputs)
            for name in handler_inputs
        }

        fact_texts = _extract_fact_texts(resolved_inputs.get("verified_facts"))
        prompt_inputs = dict(resolved_inputs)
        prompt_inputs["verified_facts"] = _format_numbered_facts(fact_texts)

        prompt_ref = step.get_domain_field("prompt_ref") or step.prompt_ref
        if not prompt_ref:
            raise ValueError(f"Step '{step.id}' missing prompt_ref")
        if not step.model_ref:
            raise ValueError(f"Step '{step.id}' missing model_ref")

        rendered = self._render_prompt(prompt_ref, prompt_inputs, context, safe=True)
        model_id = self._resolve_model_alias(step.model_ref, context)
        generation_parameters = step.generation_parameters or {}
        result = await self._call_model(
            model_id,
            rendered.user_prompt,
            step,
            context,
            system_prompt=rendered.system_prompt,
            temperature=generation_parameters.get("temperature", 0.1),
            max_tokens=generation_parameters.get("max_tokens"),
        )

        raw_output = result.content.strip()
        total_facts = len(fact_texts)
        sections_created = 0
        facts_assigned = 0
        valid_json = False

        try:
            parsed = json.loads(strip_json_fences(raw_output))
            sections = parsed.get("sections") if isinstance(parsed, dict) else None
            assigned = _collect_assigned_indices(sections)
            sections_created = len(sections) if isinstance(sections, list) else 0
            facts_assigned = len(set(assigned))
            valid_json = (
                isinstance(sections, list)
                and total_facts > 0
                and facts_assigned == total_facts
                and max(assigned, default=0) <= total_facts
                and len(assigned) == len(set(assigned))
            )
        except json.JSONDecodeError:
            logger.warning("Step '%s': organize_facts returned invalid JSON", step.id)

        recorder = context.recorder
        if recorder:
            from systems.pipeline.core.events.verification import (
                OrganizeFactsCompleted as RecorderOrganizeFactsCompleted,
            )

            recorder.emit(
                RecorderOrganizeFactsCompleted(
                    step_name=step.id,
                    total_facts=total_facts,
                    sections_created=sections_created,
                    facts_assigned=facts_assigned,
                    valid_json=valid_json,
                )
            )

        pipeline_id = str(getattr(context, "pipeline_id", "") or "")
        execution_id = str(getattr(context, "execution_id", "") or "")
        from systems.pipeline.core.events.step import (
            OrganizeFactsCompleted as BusOrganizeFactsCompleted,
        )

        self._publish_bus_event(
            context,
            BusOrganizeFactsCompleted(
                pipeline_id=pipeline_id,
                execution_id=execution_id,
                step_name=step.id,
                total_facts=total_facts,
                sections_created=sections_created,
                facts_assigned=facts_assigned,
                valid_json=valid_json,
            ),
        )

        latency_ms = (time.time() - start_time) * 1000
        return StepOutput(
            raw=raw_output,
            step_id=step.id,
            latency_ms=latency_ms,
            prompt_tokens=result.prompt_tokens,
            completion_tokens=result.completion_tokens,
            model_call_count=1,
        )
