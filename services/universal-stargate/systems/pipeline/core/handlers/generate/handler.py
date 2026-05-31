"""
Generic generate handler class.

Domain-agnostic handler that works for any pipeline type using structured
PromptConfig for configuration. Domains can override ``_build_prompt_context``
for custom context building.

Model resolution: primary model from ``model_ref`` (models.yaml), with automatic
fallback to ``model_requirements``-resolved alternatives on ``ProxyClientError``.

The hook methods (``_render_user_prompt``, ``_build_prompt_context``,
``_format_for_prompt``, ``_extract_source_provenance``,
``_inject_provenance_into_claims``, ``_resolve_execution_config_for_model``,
``_build_step_output``) are thin delegators to free-function implementations in
the package's other submodules. Each delegator that depends on another hook
passes the bound method as a callable so subclass overrides reached via
``self.<hook>`` are still honored when called transitively (e.g. an override of
``_format_for_prompt`` propagates through ``_build_prompt_context``).
"""

# ruff: noqa: E501

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from universal_logging import get_logger

from ...execution.fallback_eligibility import get_fallback_suppression_reason
from ..builtin import BaseHandler
from ..protocol import StepOutput
from ..registry import register_handler
from .invoke import (
    build_step_output,
    invoke_model_streaming,
    resolve_execution_config_for_model,
)
from .model_resolution import resolve_primary_model
from .prompt_context import build_prompt_context, format_for_prompt, render_user_prompt
from .provenance import extract_source_provenance, inject_provenance_into_claims
from .routing_errors import _annotate_routing_mismatch_error

if TYPE_CHECKING:
    from ...schemas import PromptConfig, StepConfig
    from ..builtin.types import ModelCallResult
    from ..protocol import PipelineContext

logger = get_logger(__name__)


@register_handler
class GenericGenerateHandler(BaseHandler):
    """
    Generic generate handler.

    Works for any domain - uses structured PromptConfig for configuration.
    Domains can override ``_build_prompt_context`` for custom context building.

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
        from ...execution.proxy_client import ProxyClientError

        registry = context._registry
        prompt_config = registry.get_prompt(step.prompt_ref)
        user_prompt = self._render_user_prompt(prompt_config, step, context)
        source_provenance = self._extract_source_provenance(step, context)

        model_id, model_profile, primary_resolution = await resolve_primary_model(
            step, context
        )

        # Streaming branch: when both signals fire (terminal-passthrough
        # eligible pipeline ∧ outer request set stream=True), bypass the
        # buffered _invoke_model path and open an SSE stream. The defensive
        # ``not step.is_map_step`` check is redundant with the eligibility
        # predicate (which already excludes map steps) but documents the
        # invariant locally and protects against future predicate drift.
        # No model fallback on this branch — see invoke_model_streaming
        # docstring.
        outer_stream = context.runtime_options.get("stream", False)
        if (
            outer_stream
            and context.pipeline.is_stream_passthrough_eligible
            and not step.is_map_step
        ):
            return await invoke_model_streaming(
                self,
                step,
                context,
                prompt_config,
                model_id,
                user_prompt,
                source_provenance,
                model_profile=model_profile,
            )

        try:
            return await self._invoke_model(
                step,
                context,
                prompt_config,
                model_id,
                user_prompt,
                source_provenance,
                model_profile=model_profile,
            )
        except ProxyClientError as primary_err:
            # Re-resolve executor_override here (rather than threading it back
            # through resolve_primary_model) so the explicit boolean check
            # matches the original semantics. The dict lookup is idempotent.
            executor_override = context._step_model_override.get(step.name)
            if executor_override or not step.model_requirements:
                raise

            suppression_reason = get_fallback_suppression_reason(
                primary_resolution=primary_resolution,
                model_requirements=step.model_requirements,
            )
            if suppression_reason:
                _annotate_routing_mismatch_error(
                    primary_err=primary_err,
                    step=step,
                    primary_model=model_id,
                    primary_resolution=primary_resolution,
                )
                raise

            from ..model_fallback import resolve_fallback_models, try_fallbacks

            fallback_ids = await resolve_fallback_models(
                step,
                context,
                exclude=model_id,
                primary_resolution=primary_resolution,
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
        user_prompt: str,
        source_provenance: dict[str, Any] | None,
        *,
        model_profile: str | None = None,
    ) -> StepOutput:
        """Invoke a single model and build the StepOutput.

        Called both for the primary model (from ``execute``) and for each
        fallback candidate (from ``model_fallback.try_fallbacks``, which
        passes ``handler._invoke_model`` positionally). This MUST remain on
        the class with this exact signature.
        """
        start_time = time.time()

        resolved = self._resolve_execution_config_for_model(
            step,
            prompt_config,
            model_id,
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

    # ------------------------------------------------------------------
    # Hook delegators
    #
    # Each method below is a documented override hook. The class method
    # form is preserved so domain subclasses can override on the class;
    # the body delegates to the free-function implementation, threading
    # bound methods through as callables where another hook needs to
    # dispatch through MRO.
    # ------------------------------------------------------------------

    def _resolve_execution_config_for_model(
        self,
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
        """
        return resolve_execution_config_for_model(
            self, step, prompt_config, model_id, context
        )

    def _render_user_prompt(
        self,
        prompt_config: PromptConfig,
        step: StepConfig,
        context: PipelineContext,
    ) -> str:
        """Render user prompt from template.

        Single responsibility: Prompt rendering.
        Calls ``self._build_prompt_context`` (threaded through as a callable
        below) which can be overridden by domain handlers.

        Raises:
            ValueError: If rendered prompt is empty, whitespace-only, or unfilled
        """
        return render_user_prompt(
            prompt_config,
            step,
            context,
            prompt_builder=self._prompt_builder,
            build_context=self._build_prompt_context,
        )

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
        return build_prompt_context(step, context, format_value=self._format_for_prompt)

    def _format_for_prompt(self, value: Any, field_name: str) -> str:
        """
        Format value for prompt template (avoid JSON, use plain text).

        Arrays are formatted as numbered lists for better LLM comprehension.
        Simple string lists (like theme_words) are formatted as comma-separated.
        """
        return format_for_prompt(value, field_name)

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
        return extract_source_provenance(step, context)

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
        return inject_provenance_into_claims(
            json_data,
            source_provenance,
            processor_model_id=processor_model_id,
            processor_step_id=processor_step_id,
        )

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
        return build_step_output(
            call_result,
            resolved_config,
            latency_ms,
            step_id,
            source_provenance=source_provenance,
            inject_provenance=self._inject_provenance_into_claims,
        )

    # ------------------------------------------------------------------
    # Validation / introspection
    # ------------------------------------------------------------------

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
