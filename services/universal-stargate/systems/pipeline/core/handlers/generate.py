"""
Generic generate handler.

Domain-agnostic handler that works for any pipeline type using structured
PromptConfig for configuration. Domains can override _build_prompt_context()
for custom context building.

Model resolution: primary model from model_ref (models.yaml), with automatic
fallback to model_requirements-resolved alternatives on ProxyClientError.
"""
# ruff: noqa: E501

from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING, Any

from universal_logging import get_logger

from .builtin import BaseHandler
from .protocol import StepOutput
from .registry import register_handler

if TYPE_CHECKING:
    from ..execution.resolver import NamespaceResolver
    from ..schemas import PromptConfig, StepConfig
    from .builtin.types import ModelCallResult
    from .protocol import PipelineContext

logger = get_logger(__name__)


def _strip_markdown_fence(text: str) -> str:
    """Extract JSON content from model responses.

    Cloud providers (notably Anthropic) may return JSON wrapped in
    markdown fences and/or preceded by preamble text ("Let me analyze...")
    even when response_format: json_object was requested. This extracts
    the JSON object so json.loads succeeds.

    Extraction order:
    1. If text starts with ```, strip the fence
    2. If a fenced JSON block appears anywhere, extract it
    3. If a bare JSON object ({...}) appears after preamble, extract it
    4. Return stripped text as-is (let json.loads report the error)
    """
    import re

    stripped = text.strip()

    if stripped.startswith("```"):
        match = re.match(r"```(?:json|\w*)\s*\n(.*)```", stripped, re.DOTALL)
        if match:
            return match.group(1).strip()

    fence_match = re.search(
        r"```(?:json|\w*)\s*\n(.*?)```", stripped, re.DOTALL
    )
    if fence_match:
        return fence_match.group(1).strip()

    brace_match = re.search(r"(\{.*\})", stripped, re.DOTALL)
    if brace_match:
        return brace_match.group(1).strip()

    return stripped


def _resolve_avoid_models(
    binding_path: str,
    resolver: NamespaceResolver,
    step_name: str,
) -> list[str]:
    """Resolve avoid_models_from binding path to model IDs to exclude."""
    parts = binding_path.split(".", 1)
    step_ref = parts[0]
    field_path = parts[1] if len(parts) > 1 else "model_id"

    from ..execution.resolver import traverse_path
    from ..schemas import InputBinding

    binding = InputBinding(
        namespace="step",
        step_name=step_ref,
        field_path=field_path,
    )
    try:
        root = resolver.resolve(binding)
        value = traverse_path(
            root,
            field_path,
            step_name=step_name,
            field_name="avoid_models_from",
            binding_repr=binding_path,
            resolver=resolver,
        )
    except (KeyError, AttributeError, ValueError) as exc:
        logger.warning(
            "[%s] Failed resolving avoid_models_from=%s: %s",
            step_name,
            binding_path,
            exc,
        )
        return []

    if not value:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(v) for v in value if v]
    return []


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
        """Execute generate step with model fallback on failure.

        Model resolution order:
        1. Executor-level override via context._step_model_override (full ID,
           set by DAGExecutor during step-level fallback for timeout/any error)
        2. model_ref_overrides (explicit caller choice, e.g. --models flag)
        3. model_ref "auto" or absent + model_requirements → /v1/models/select
        4. model_ref → models.yaml registry lookup
        5. On ProxyClientError, handler-level fallback via model_requirements
        """
        from ..execution.proxy_client import ProxyClientError

        registry = context._registry
        prompt_config = registry.get_prompt(step.prompt_ref)
        user_prompt = self._render_user_prompt(prompt_config, step, context)
        source_provenance = self._extract_source_provenance(step, context)

        executor_override = context._step_model_override.get(step.name)
        _raw_overrides = context.options.get("model_ref_overrides")
        model_ref_overrides: dict[str, Any] = (
            _raw_overrides if isinstance(_raw_overrides, dict) else {}
        )
        runtime_override = (
            model_ref_overrides.get(step.name)
            or model_ref_overrides.get(step.model_ref)
            if step.model_ref
            else None
        )
        if runtime_override:
            runtime_override = runtime_override.strip()
        elif model_ref_overrides and step.model_ref:
            override_keys = (
                list(model_ref_overrides.keys())
                if isinstance(model_ref_overrides, dict)
                else "n/a"
            )
            logger.debug(
                "[%s] model_ref_overrides present but no match for "
                "step.name=%r or step.model_ref=%r; keys=%s",
                step.name,
                step.name,
                step.model_ref,
                override_keys,
            )

        if executor_override:
            model_id = executor_override
            model_system_prompt = None
            model_profile = None
        elif runtime_override:
            model_id = runtime_override
            model_system_prompt = None
            model_profile = None
            logger.info("[%s] Using runtime model override: %s", step.name, model_id)
        elif step.model_ref == "auto" or (
            not step.model_ref and step.model_requirements
        ):
            from ..execution.resolved_candidates import get_ranked_candidates
            from ..execution.resolver import NamespaceResolver

            requirements = dict(step.model_requirements or {})
            if step.avoid_models_from:
                try:
                    resolver = NamespaceResolver(context)
                    avoided = _resolve_avoid_models(
                        step.avoid_models_from,
                        resolver,
                        step.name,
                    )
                    if avoided:
                        existing = requirements.get("avoid_models")
                        if isinstance(existing, list):
                            merged = [str(item) for item in existing if item]
                        elif isinstance(existing, str) and existing:
                            merged = [existing]
                        else:
                            merged = []
                        deduped = list(dict.fromkeys(merged + avoided))
                        requirements["avoid_models"] = deduped
                except (KeyError, AttributeError, ValueError) as exc:
                    logger.warning(
                        "[%s] avoid_models_from resolution failed for '%s': %s. "
                        "Proceeding without model exclusion.",
                        step.name,
                        step.avoid_models_from,
                        exc,
                    )

            candidates = await get_ranked_candidates(
                context=context,
                step_name=step.name,
                requirements=requirements,
            )
            if not candidates:
                logger.warning(
                    "[%s] Auto model resolution returned no candidates "
                    "for requirements=%s",
                    step.name,
                    requirements,
                )
                raise ValueError(
                    f"Step '{step.name}': auto model resolution found no candidates "
                    f"for requirements {requirements}. "
                    f"Check that models matching the requirements are available "
                    f"(source/task/min_score filters may be too restrictive, or "
                    f"the /v1/models/select endpoint may be temporarily unavailable)."
                )
            model_id = candidates[0]
            model_system_prompt = None
            model_profile = None
            logger.info(
                "[%s] Auto-resolved model from requirements: %s",
                step.name,
                model_id,
            )
        else:
            try:
                model_config = registry.get_model_config(
                    step.model_ref,
                    domain=context.pipeline.domain,
                    search_path=context.pipeline.source_search_path,
                )
                model_id = model_config.model
                model_system_prompt = model_config.system_prompt
                model_profile = model_config.profile
            except KeyError:
                model_id = step.model_ref
                model_system_prompt = None
                model_profile = None
                logger.info(
                    "[%s] Using raw model ID (not in models.yaml): %s",
                    step.name,
                    model_id,
                )

        try:
            return await self._invoke_model(
                step,
                context,
                prompt_config,
                model_id,
                model_system_prompt,
                user_prompt,
                source_provenance,
                model_profile=model_profile,
            )
        except ProxyClientError as primary_err:
            if executor_override or not step.model_requirements:
                raise

            from .model_fallback import resolve_fallback_models, try_fallbacks

            fallback_ids = await resolve_fallback_models(
                step,
                context,
                exclude=model_id,
            )
            if not fallback_ids:
                raise

            logger.warning(
                "[%s] Primary model '%s' failed (%s), trying %d fallback(s)",
                step.name,
                model_id,
                primary_err,
                len(fallback_ids),
            )
            return await try_fallbacks(
                self,
                step,
                context,
                prompt_config,
                user_prompt,
                source_provenance,
                fallback_ids,
                primary_model=model_id,
                primary_error=str(primary_err),
                last_error=primary_err,
            )

    async def _invoke_model(
        self,
        step: StepConfig,
        context: PipelineContext,
        prompt_config: PromptConfig,
        model_id: str,
        model_system_prompt: str | None,
        user_prompt: str,
        source_provenance: dict[str, Any] | None,
        *,
        model_profile: str | None = None,
    ) -> StepOutput:
        """Invoke a single model and build the StepOutput."""
        start_time = time.time()

        resolved = self._resolve_execution_config_for_model(
            step,
            prompt_config,
            model_id,
            model_system_prompt,
            context,
        )

        logger.debug(
            "Generate step '%s': model=%s, temp=%s, max_tokens=%s",
            step.id,
            resolved["model_id"],
            resolved["temperature"],
            resolved["max_tokens"],
        )

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
            model_profile=model_profile,
        )

        latency_ms = (time.time() - start_time) * 1000
        return self._build_step_output(
            call_result,
            resolved,
            latency_ms,
            step.id,
            source_provenance=source_provenance,
        )

    def _resolve_execution_config_for_model(
        self,
        step: StepConfig,
        prompt_config: PromptConfig,
        model_id: str,
        model_system_prompt: str | None,
        context: PipelineContext,
    ) -> dict[str, Any]:
        """Resolve execution configuration for a specific model.

        System prompt hierarchy: prompt > model > "".
        System prompt is rendered with the same template context as the user prompt
        so placeholders (e.g. {corpus_hints}, {scope_options}) are substituted.
        Generation parameters hierarchy: step > token_defaults > dynamic.
        """
        system_prompt_raw = prompt_config.system_prompt or model_system_prompt or ""
        if system_prompt_raw:
            prompt_context = self._build_prompt_context(step, context)
            system_prompt = self._prompt_builder.render_safe(
                system_prompt_raw, prompt_context
            )
        else:
            system_prompt = ""
        temperature = step.generation_parameters.get("temperature")
        max_tokens = self._resolve_max_tokens(step, context)

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

        # Validate all required placeholders resolve in context
        # Uses _resolve_path (supports dotted paths like {retrieval.chunks})
        missing_placeholders = self._prompt_builder.validate_context(
            prompt_config.template, prompt_context
        )
        if missing_placeholders:
            available_keys = list(prompt_context.keys())
            raise ValueError(
                f"Step '{step.id}': Template has unfilled placeholders: "
                f"{sorted(missing_placeholders)}. "
                f"Available context keys: {sorted(available_keys)}. "
                f"Check that handler_inputs are correctly configured and "
                f"dependency steps have completed."
            )

        for placeholder in required_placeholders:
            value = self._prompt_builder._resolve_path(placeholder, prompt_context)
            if value is None or (isinstance(value, str) and not value.strip()):
                raise ValueError(
                    f"Step '{step.id}': Required placeholder '{placeholder}' is empty or None. "
                    f"Value: {repr(value)}. Check handler_inputs and dependency steps."
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
        # scope_options may be a list in pipeline YAML; prompt template expects string
        if isinstance(prompt_context.get("scope_options"), list):
            prompt_context["scope_options"] = "\n".join(
                f'    "{x}"' for x in prompt_context["scope_options"]
            )

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
                except (KeyError, AttributeError, ValueError) as e:
                    logger.error(
                        "Step '%s': Failed to resolve handler_input '%s' "
                        "(binding=%s): %s. Input will be absent from prompt context.",
                        step.id,
                        field_name,
                        binding,
                        e,
                        exc_info=True,
                    )
        else:
            logger.debug(f"Step '{step.id}': No handler_inputs to resolve")

        # Merge pre-resolved map_inputs (from MapExecutor for iteration-specific values)
        # Dicts are kept as-is so dotted template paths (e.g. {retrieval.chunks})
        # resolve correctly through PromptBuilder._resolve_path.
        if step.resolved_map_inputs:
            for field_name, value in step.resolved_map_inputs.items():
                if isinstance(value, dict):
                    prompt_context[field_name] = value
                else:
                    prompt_context[field_name] = self._format_for_prompt(
                        value, field_name
                    )
                logger.debug(
                    f"Step '{step.id}': Added resolved map_input '{field_name}' "
                    f"({len(str(value))} chars)"
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

            if all(isinstance(item, str) and len(item) < 50 for item in value):
                return ", ".join(value)

            lines = []
            for i, item in enumerate(value):
                if isinstance(item, dict):
                    parts = [f"{k}: {v}" for k, v in item.items()]
                    lines.append(f"{i}: {', '.join(parts)}")
                elif isinstance(item, str):
                    lines.append(
                        f"{i}: {item if item.strip() else '(paragraph break)'}"
                    )
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
        if not getattr(context, "_map_state", None):
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
            specific_output = (
                source_output.get_output_by_key(iteration_key)
                if iteration_key
                else source_output.get_output(map_state.iteration_index)
            )
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
        call_result: ModelCallResult,
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

        json_data: dict[str, Any] | None = None
        json_parse_error: str | None = None
        if resolved_config.get("wants_json"):
            try:
                content_for_parse = _strip_markdown_fence(call_result.content)
                json_data = json.loads(content_for_parse)

                # Inject provenance into claims if source_provenance provided
                if source_provenance and json_data:
                    json_data = self._inject_provenance_into_claims(
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
            system_prompt=call_result.system_prompt,
            user_prompt=call_result.user_prompt,
            temperature=resolved_config.get("temperature"),
            max_tokens=resolved_config.get("max_tokens"),
            request_body=call_result.request_body,
        )

    def validate(self, step: StepConfig) -> list[str]:
        """Validate required config for generate step.

        Args:
            step: Step configuration.

        Returns:
            Validation error messages (empty when valid).
        """
        errors = []
        if not step.model_ref and not step.model_requirements:
            errors.append(f"Step '{step.id}' needs model_ref or model_requirements")
        if not step.prompt_ref:
            errors.append(f"Step '{step.id}' missing prompt_ref")
        return errors

    def get_required_placeholders(self) -> set[str]:
        """Return placeholders required by the base template context."""
        return {"text"}
