"""Single model call handler — prompt in, text out, no loop."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any, cast, override

from systems.pipeline.core.execution.resolver import NamespaceResolver
from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput

if TYPE_CHECKING:
    from systems.pipeline.core.handlers.protocol import PipelineContext
    from systems.pipeline.core.schemas import StepConfig


class SingleCallHandler(BaseHandler):
    """Render a prompt from handler_inputs, call model once, return text."""

    step_type: str = "consensus_single_call_v8_0"

    @override
    async def execute(self, step: StepConfig, context: PipelineContext) -> StepOutput:
        start_time = time.time()
        resolver = NamespaceResolver(cast(Any, context))
        handler_inputs = step.handler_inputs or {}

        resolved_inputs: dict[str, Any] = {
            name: self._resolve_input(resolver, step, name, handler_inputs)
            for name in handler_inputs
        }
        for key, value in resolved_inputs.items():
            if (
                isinstance(value, list)
                and value
                and isinstance(value[0], dict)
                and "text" in value[0]
            ):
                resolved_inputs[key] = "\n".join(
                    f"[{index}] {item['text']}"
                    for index, item in enumerate(value, 1)
                    if item.get("text")
                )

        prompt_ref = step.get_domain_field("prompt_ref") or step.prompt_ref
        if not prompt_ref:
            raise ValueError(f"Step '{step.id}' missing prompt_ref")
        if not step.model_ref:
            raise ValueError(f"Step '{step.id}' missing model_ref")

        rendered = self._render_prompt(prompt_ref, resolved_inputs, context, safe=True)
        model_id = self._resolve_model_alias(step.model_ref, context)
        generation_parameters = step.generation_parameters or {}
        result = await self._call_model(
            model_id,
            rendered.user_prompt,
            step,
            context,
            system_prompt=rendered.system_prompt,
            temperature=generation_parameters.get("temperature", 0.3),
            max_tokens=generation_parameters.get("max_tokens"),
        )

        latency_ms = (time.time() - start_time) * 1000
        return StepOutput(
            raw=result.content.strip(),
            step_id=step.id,
            latency_ms=latency_ms,
            prompt_tokens=result.prompt_tokens,
            completion_tokens=result.completion_tokens,
            model_call_count=1,
        )
