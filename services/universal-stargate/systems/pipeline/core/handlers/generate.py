"""
Generic generate handler.

Domain-agnostic handler that works for any pipeline type using structured
PromptConfig for configuration. Domains can override _build_prompt_context()
for custom context building.
"""

from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING, Any

from universal_logging import get_logger

from .builtin import BaseHandler
from .protocol import StepOutput
from .registry import register_handler

if TYPE_CHECKING:
    from ..schemas import PromptConfig, StepConfig
    from .protocol import PipelineContext

logger = get_logger(__name__)


@register_handler
class GenericGenerateHandler(BaseHandler):
    """
    Generic generate handler.

    Works for any domain - uses structured PromptConfig for configuration.
    Domains can override _build_prompt_context() for custom context building.

    Returns StepOutput; does NOT write to context.
    """

    step_type = "generate"

    async def execute(
        self,
        step: StepConfig,
        context: PipelineContext,
    ) -> StepOutput:
        """Execute generate step using structured prompt configuration."""
        start_time = time.time()

        # Load configurations
        registry = context._registry
        prompt_config = registry.get_prompt(step.prompt_ref)

        model_config = registry.get_model_config(
            step.model_ref,
            domain=context.pipeline.domain,
            search_path=context.pipeline.source_search_path,
        )

        # Resolve configuration hierarchy (includes dynamic token adjustment)
        resolved = self._resolve_execution_config(
            step, prompt_config, model_config, context
        )

        # Render user prompt
        user_prompt = self._render_user_prompt(prompt_config, step, context)

        logger.debug(
            f"Generate step '{step.id}': "
            f"model={resolved['model_id']}, "
            f"temp={resolved['temperature']}, "
            f"max_tokens={resolved['max_tokens']}"
        )

        # Extract source provenance (for map steps processing answers)
        source_provenance = self._extract_source_provenance(step, context)

        # Invoke model - returns ModelCallResult (concurrency safety)
        # Handler uses return value, not instance fields (safe for map iterations)
        # Pre-2026-01-31: used self._last_* (race condition in parallel map steps)
        call_result = await self._call_model(
            resolved["model_id"],
            user_prompt,
            step,
            context,
            resolved["system_prompt"],
            temperature=resolved["temperature"],
            max_tokens=resolved["max_tokens"],
            json_schema=resolved["json_schema"],
            model_id_is_resolved=True,
        )

        # Build output from result
        latency_ms = (time.time() - start_time) * 1000
        return self._build_step_output(
            call_result,
            resolved,
            latency_ms,
            step.id,
            source_provenance=source_provenance,
        )

    def _resolve_execution_config(
        self,
        step: StepConfig,
        prompt_config: PromptConfig,
        model_config: Any,
        context: PipelineContext,
    ) -> dict[str, Any]:
        """
        Resolve execution configuration.

        Generation parameters hierarchy:
        1. Step generation_parameters (explicit)
        2. Pipeline token_defaults (fallback)
        3. Dynamic adjustment based on expansion_safe

        System prompt uses hierarchy: prompt > model > "".
        """
        # System prompt hierarchy (unchanged)
        system_prompt = prompt_config.system_prompt or model_config.system_prompt or ""

        # Generation parameters: step config with token_defaults fallback
        temperature = step.generation_parameters.get("temperature")
        max_tokens = self._resolve_max_tokens(step, context)

        # JSON schema: step response_format OR prompt json_schema (compatibility)
        json_schema = None
        if step.generation_parameters.get("response_format"):
            json_schema = step.generation_parameters["response_format"].get("schema")
        elif prompt_config.json_schema:
            json_schema = prompt_config.json_schema

        return {
            "model_id": model_config.model,
            "system_prompt": system_prompt,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "json_schema": json_schema,
        }

    # _resolve_max_tokens and _infer_token_category live on BaseHandler
    # so all domain handlers can use them.

    def _render_user_prompt(
        self,
        prompt_config: PromptConfig,
        step: StepConfig,
        context: PipelineContext,
    ) -> str:
        """
        Render user prompt from template.

        Single responsibility: Prompt rendering.
        Calls _build_prompt_context() which can be overridden by domain handlers.

        Raises:
            ValueError: If rendered prompt is empty, whitespace-only, or unfilled
        """
        prompt_context = self._build_prompt_context(step, context)

        # Get required placeholders
        required_placeholders = self._prompt_builder.get_placeholders(
            prompt_config.template
        )

        # Validate all required placeholders are in context
        missing_placeholders = required_placeholders - set(prompt_context.keys())
        if missing_placeholders:
            available_keys = list(prompt_context.keys())
            raise ValueError(
                f"Step '{step.id}': Template has unfilled placeholders: "
                f"{sorted(missing_placeholders)}. "
                f"Available context keys: {sorted(available_keys)}. "
                f"Check that handler_inputs are correctly configured and "
                f"dependency steps have completed."
            )

        # Check for empty values in critical placeholders
        for placeholder in required_placeholders:
            value = prompt_context.get(placeholder)
            if value is None or (isinstance(value, str) and not value.strip()):
                logger.warning(
                    f"Step '{step.id}': Placeholder '{placeholder}' is empty or None. "
                    f"Value: {repr(value)}"
                )

        rendered = self._prompt_builder.render_safe(
            prompt_config.template, prompt_context
        )

        # Validate rendered prompt is not empty
        if not rendered or not rendered.strip():
            available_keys = list(prompt_context.keys())
            raise ValueError(
                f"Step '{step.id}': Rendered user prompt is empty. "
                f"Template requires: {sorted(required_placeholders)}, "
                f"but context has: {sorted(available_keys)}. "
                f"This usually means dependency steps haven't completed or "
                f"placeholders don't match step IDs."
            )
        return rendered

    def _build_prompt_context(
        self,
        step: StepConfig,
        context: PipelineContext,
    ) -> dict[str, Any]:
        """
        Build context dictionary for prompt template rendering.

        Override in domain handlers for domain-specific context variables.
        Base implementation provides: text, plus all pipeline options.
        Also resolves handler_inputs and adds them to context.

        CRITICAL: Automatically merges step.resolved_map_inputs into prompt context.
        When using map_inputs, fields are available directly in template:

        Example:
            map_inputs:
              prose_text: mapNs.iteration.value.text

            # Automatically available in template as {prose_text}
            template: |
              Reformat: {prose_text}

        Subclasses extending GenericGenerateHandler don't need to manually
        handle template variables - just validate and call super().execute().
        """
        from ..execution.resolver import NamespaceResolver, traverse_path

        prompt_context = {
            "text": context.source_text,
            **context.options,
        }

        # Resolve handler_inputs and add to prompt context
        if step.handler_inputs:
            logger.info(
                f"Step '{step.id}': Resolving {len(step.handler_inputs)} handler_inputs"
            )
            resolver = NamespaceResolver(context)
            for field_name, binding in step.handler_inputs.items():
                try:
                    logger.debug(
                        f"Step '{step.id}': Resolving handler_input '{field_name}' "
                        f"from binding: {binding}"
                    )
                    root = resolver.resolve(binding)
                    value = traverse_path(
                        root,
                        binding.field_path,
                        step_name=step.id,
                        field_name=field_name,
                        binding_repr=str(binding),
                        resolver=resolver,
                    )
                    logger.debug(
                        f"Step '{step.id}': Resolved '{field_name}' to type "
                        f"{type(value).__name__}"
                    )
                    # Format arrays as plain text (models struggle with JSON in prompts)
                    formatted_value = self._format_for_prompt(value, field_name)
                    prompt_context[field_name] = formatted_value
                    logger.info(
                        f"Step '{step.id}': Added '{field_name}' to prompt context "
                        f"({len(str(formatted_value))} chars)"
                    )
                except Exception as e:
                    logger.error(
                        f"Step '{step.id}': Failed to resolve handler_input "
                        f"'{field_name}': {e}",
                        exc_info=True,
                    )
                    # Continue with other inputs rather than failing
        else:
            logger.debug(f"Step '{step.id}': No handler_inputs to resolve")

        # Merge pre-resolved map_inputs (from MapExecutor for iteration-specific values)
        if step.resolved_map_inputs:
            for field_name, value in step.resolved_map_inputs.items():
                formatted_value = self._format_for_prompt(value, field_name)
                prompt_context[field_name] = formatted_value
                logger.debug(
                    f"Step '{step.id}': Added resolved map_input '{field_name}' "
                    f"({len(str(formatted_value))} chars)"
                )

        return prompt_context

    def _format_for_prompt(self, value: Any, field_name: str) -> str:
        """
        Format value for prompt template (avoid JSON, use plain text).

        Arrays are formatted as numbered lists for better LLM comprehension.
        Simple string lists (like theme_words) are formatted as comma-separated.
        """
        if isinstance(value, list):
            if not value:
                return "(empty array)"

            # Simple string lists (like theme_words) - format as comma-separated
            if all(isinstance(item, str) and len(item) < 50 for item in value):
                return ", ".join(value)

            # Complex arrays - format as numbered list (0-based indices)
            lines = []
            for i, item in enumerate(value):
                if isinstance(item, dict):
                    # Format dict items as key: value pairs
                    parts = [f"{k}: {v}" for k, v in item.items()]
                    lines.append(f"{i}: {', '.join(parts)}")
                elif isinstance(item, str):
                    # Empty strings represent paragraph breaks
                    if not item.strip():
                        lines.append(f"{i}: (paragraph break)")
                    else:
                        lines.append(f"{i}: {item}")
                else:
                    lines.append(f"{i}: {item}")
            return "\n".join(lines)

        if isinstance(value, dict):
            # Format dict as key: value pairs
            return "\n".join(f"{k}: {v}" for k, v in value.items())

        # For simple values (strings, numbers, etc.), return as-is
        return str(value) if value is not None else ""

    def _extract_source_provenance(
        self,
        step: StepConfig,
        context: PipelineContext,
    ) -> dict[str, Any] | None:
        """
        Extract provenance from source data (for map steps processing answers).

        For decompose_all mapping over answer_all.*:
        - Source is answer_all.{key} output
        - Source provenance = answer author (originator)

        Returns None if no source provenance found.
        """
        # Check if this is a map step with iteration context
        if not hasattr(context, "_map_state") or not context._map_state:
            return None

        map_state = context._map_state

        # Get the source step output (e.g., answer_all.phi)
        source_step_name = map_state.source_step_name  # e.g., "answer_all"
        iteration_key = map_state.iteration_key  # e.g., "phi"

        if not source_step_name:
            return None

        # Resolve source output
        source_output = context.get_output(source_step_name)
        if not source_output:
            return None

        # Handle MapOutputCollection
        from ..execution.map_reduce import MapOutputCollection

        if isinstance(source_output, MapOutputCollection):
            if iteration_key:
                specific_output = source_output.get_output_by_key(iteration_key)
                if specific_output and specific_output.provenance:
                    return specific_output.provenance
            else:
                # Fallback to index-based for list iterations
                specific_output = source_output.get_output(map_state.iteration_index)
                if specific_output and specific_output.provenance:
                    return specific_output.provenance
        elif hasattr(source_output, "provenance") and source_output.provenance:
            return source_output.provenance

        return None

    def _inject_provenance_into_claims(
        self,
        json_data: dict[str, Any],
        source_provenance: dict[str, Any],
        processor_model_id: str,
        processor_step_id: str,
    ) -> dict[str, Any]:
        """
        Inject provenance into claim objects within JSON response.

        Handles common claim container patterns:
        - {"claims": [...]}
        - {"statements": [...]}
        - {"evaluations": [...]}

        Each claim gets:
        - originator from source_provenance
        - processor added to lineage
        """
        from provenance import Provenance

        # Build claim provenance (source originator + this processor)
        prov = Provenance.from_dict(source_provenance)
        prov = prov.with_processor(
            step_id=processor_step_id,
            processor_model_id=processor_model_id,
        )
        claim_provenance = prov.to_dict()

        # Inject into known claim containers
        for key in ("claims", "statements", "evaluations"):
            if key in json_data and isinstance(json_data[key], list):
                for item in json_data[key]:
                    if isinstance(item, dict) and "provenance" not in item:
                        item["provenance"] = claim_provenance

        return json_data

    def _build_step_output(
        self,
        call_result,
        resolved_config: dict[str, Any],
        latency_ms: float,
        step_id: str,
        source_provenance: dict[str, Any] | None = None,
    ) -> StepOutput:
        """
        Build StepOutput from model call result.

        Args:
            call_result: Complete result from _call_model() (ModelCallResult)
            resolved_config: Configuration used (model_id, temperature, etc.)
            latency_ms: Execution latency
            step_id: Step identifier for provenance
            source_provenance: Optional provenance from source (for processors)
        """
        from provenance import Provenance, create_provenance

        json_data = None
        if resolved_config["json_schema"]:
            try:
                json_data = json.loads(call_result.content)

                # Inject provenance into claims if source_provenance provided
                if source_provenance and json_data:
                    json_data = self._inject_provenance_into_claims(
                        json_data,
                        source_provenance,
                        processor_model_id=resolved_config["model_id"],
                        processor_step_id=step_id,
                    )

            except json.JSONDecodeError as e:
                logger.warning(
                    f"Expected JSON response but parsing failed: {e}. "
                    f"Raw (first 200 chars): {call_result.content[:200]}..."
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
            model_id=resolved_config["model_id"],
            step_id=step_id,
            provenance=output_provenance,
            latency_ms=latency_ms,
            prompt_tokens=call_result.prompt_tokens,
            completion_tokens=call_result.completion_tokens,
            system_prompt=call_result.system_prompt,
            user_prompt=call_result.user_prompt,
            temperature=resolved_config.get("temperature"),
            max_tokens=resolved_config.get("max_tokens"),
            request_body=call_result.request_body,
        )

    def validate(self, step: StepConfig) -> list[str]:
        errors = []
        if not step.model_ref:
            errors.append(f"Step '{step.id}' missing model_ref")
        if not step.prompt_ref:
            errors.append(f"Step '{step.id}' missing prompt_ref")
        return errors

    def get_required_placeholders(self) -> set[str]:
        return {"text"}
