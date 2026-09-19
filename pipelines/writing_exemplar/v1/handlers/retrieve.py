"""Retrieve writing_exemplars, optionally constrained to one register prefix."""

from __future__ import annotations

from typing import Any, override

from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.pipeline_call import PipelineCallHandler
from systems.pipeline.core.handlers.protocol import StepOutput

from .register_bind import prefixes_for_register

_PIPELINE_CALL = PipelineCallHandler()


def _call_step(parent: Any, pipeline_options: dict[str, Any]) -> Any:
    consumer = parent.get_domain_field("consumer_model_ref", "")
    timeout = parent.timeout_seconds or 90

    class _CallStep:
        id = getattr(parent, "id", "retrieve_exemplars")
        handler_timeout_seconds = timeout
        timeout_seconds = timeout

        @staticmethod
        def get_domain_field(key: str, default: Any = None) -> Any:
            if key == "pipeline_id":
                return "rag-context"
            if key == "pipeline_options":
                return pipeline_options
            if key == "consumer_model_ref":
                return consumer
            return default

    return _CallStep()


class WritingExemplarRetrieveHandler(BaseHandler):
    """Map options.register onto rag-context; unknown register fails closed."""

    step_type = "writing_exemplar_retrieve_v1"

    @override
    async def execute(self, step: Any, context: Any) -> StepOutput:
        step_options = dict(step.get_domain_field("pipeline_options", {}) or {})
        prefixes = prefixes_for_register(context.options.get("register"))
        if prefixes:
            step_options["rag_source_prefixes"] = prefixes
        call_out = await _PIPELINE_CALL.execute(_call_step(step, step_options), context)
        payload = dict(call_out.json or {})
        payload["register"] = str(context.options.get("register") or "all")
        payload["rag_source_prefixes"] = prefixes
        return StepOutput(raw=call_out.raw, json=payload)
