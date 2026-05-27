"""
Per-model invocation utilities for the generate handler.

Two free functions consumed by ``GenericGenerateHandler._invoke_model``:

- ``resolve_execution_config_for_model`` — assemble the per-invocation
  config dict (resolved model_id, system_prompt rendered through the same
  template context as the user prompt, temperature, max_tokens,
  json_schema, wants_json flag). Honors the step > prompt > "" system
  prompt hierarchy and the step > token_defaults > dynamic max_tokens
  hierarchy via ``handler._resolve_max_tokens`` (inherited from
  ``BaseHandler``). Takes ``handler`` as first arg so subclass override
  of ``_build_prompt_context`` propagates.

- ``build_step_output`` — package a ``ModelCallResult`` into a
  ``StepOutput``, parsing JSON (with fence stripping for cloud providers)
  when ``wants_json`` is set, and attaching either source-derived
  processor provenance or fresh originator provenance. Takes an optional
  ``inject_provenance`` callable so subclass override of
  ``_inject_provenance_into_claims`` propagates through
  ``self._build_step_output``.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from universal_logging import get_logger

from ..protocol import StepOutput
from .fence import _strip_markdown_fence
from .provenance import inject_provenance_into_claims

if TYPE_CHECKING:
    from collections.abc import Callable

    from ...schemas import PromptConfig, StepConfig
    from ..builtin.types import ModelCallResult
    from ..protocol import PipelineContext

logger = get_logger(__name__)


def resolve_execution_config_for_model(
    handler: Any,
    step: StepConfig,
    prompt_config: PromptConfig,
    model_id: str,
    context: PipelineContext,
) -> dict[str, Any]:
    """Resolve execution configuration for a specific model.

    System prompt hierarchy: step > prompt > "".
    System prompt is rendered with the same template context as the user prompt
    so placeholders (e.g. {corpus_hints}, {scope_options}) are substituted.
    Generation parameters hierarchy: step > token_defaults > dynamic.

    Takes ``handler`` so calls to the documented ``_build_prompt_context``
    override hook (and the ``_prompt_builder`` / ``_resolve_max_tokens``
    inherited attributes from ``BaseHandler``) dispatch through MRO.
    """
    system_prompt_raw = step.system_prompt or prompt_config.system_prompt or ""
    if system_prompt_raw:
        prompt_context = handler._build_prompt_context(step, context)
        system_prompt = handler._prompt_builder.render_safe(
            system_prompt_raw, prompt_context
        )
    else:
        system_prompt = ""
    temperature = step.generation_parameters.get("temperature")
    max_tokens = handler._resolve_max_tokens(step, context)

    json_schema = None
    wants_json = False
    response_format = step.generation_parameters.get("response_format")
    if response_format:
        json_schema = response_format.get("schema")
        wants_json = response_format.get("type") == "json_object"

    return {
        "model_id": model_id,
        "system_prompt": system_prompt,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "json_schema": json_schema,
        "wants_json": wants_json,
    }


def build_step_output(
    call_result: ModelCallResult,
    resolved_config: dict[str, Any],
    latency_ms: float,
    step_id: str,
    source_provenance: dict[str, Any] | None = None,
    *,
    inject_provenance: Callable[..., dict[str, Any]] | None = None,
) -> StepOutput:
    """
    Build StepOutput from model call result.

    Args:
        call_result: Complete result from _call_model() (ModelCallResult)
        resolved_config: Configuration used (model_id, temperature, etc.)
        latency_ms: Execution latency
        step_id: Step identifier for provenance
        source_provenance: Optional provenance from source (for processors)
        inject_provenance: Optional override of ``inject_provenance_into_claims``
            so subclass override of the corresponding handler hook
            propagates through ``self._build_step_output``.
    """
    from provenance import Provenance, create_provenance

    injector = inject_provenance or inject_provenance_into_claims

    json_data: dict[str, Any] | None = None
    json_parse_error: str | None = None
    if resolved_config.get("wants_json"):
        try:
            content_for_parse = _strip_markdown_fence(call_result.content)
            json_data = json.loads(content_for_parse)

            # Inject provenance into claims if source_provenance provided
            if source_provenance and json_data:
                json_data = injector(
                    json_data,
                    source_provenance,
                    processor_model_id=resolved_config["model_id"],
                    processor_step_id=step_id,
                )

        except json.JSONDecodeError as e:
            json_parse_error = str(e)
            logger.warning(
                "Expected JSON response but parsing failed: %s. "
                "Raw (first 200 chars): %s...",
                e,
                call_result.content[:200],
            )

    # Build output provenance
    if source_provenance:
        # This step is a processor, not originator
        prov = Provenance.from_dict(source_provenance)
        prov = prov.with_processor(
            step_id=step_id,
            processor_model_id=resolved_config["model_id"],
        )
        output_provenance = prov.to_dict()
    else:
        # This step is the originator
        output_provenance = create_provenance(
            model_id=resolved_config["model_id"],
            step_id=step_id,
        ).to_dict()

    return StepOutput(
        raw=call_result.content,
        json=json_data,
        json_parse_error=json_parse_error,
        model_id=resolved_config["model_id"],
        step_id=step_id,
        provenance=output_provenance,
        latency_ms=latency_ms,
        prompt_tokens=call_result.prompt_tokens,
        completion_tokens=call_result.completion_tokens,
        reasoning=call_result.reasoning,
        reasoning_tokens=call_result.reasoning_tokens,
        system_prompt=call_result.system_prompt,
        user_prompt=call_result.user_prompt,
        temperature=resolved_config.get("temperature"),
        max_tokens=resolved_config.get("max_tokens"),
        request_body=call_result.request_body,
    )
