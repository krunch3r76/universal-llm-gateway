"""
Base handler with common utilities for model invocation.

Orchestrates helpers from sibling modules (model_resolution, prompt_rendering,
token_management, generation_params). Subclasses inherit the full method API.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from universal_logging import get_logger

from ...prompts import get_prompt_builder
from ..protocol import AbstractStepHandler, PipelineContext
from .call_model import call_model
from .generation_params import ALLOWED_GENERATION_PARAMS, _build_generation_params
from .model_resolution import (
    FULL_ID_INDICATORS,
    _get_cloud_proxy_mode,
    _get_cloud_select_fn,
    _looks_like_full_model_id,
    _resolve_model_alias,
    _resolve_model_alias_async,
    _resolve_model_pool,
)
from .prompt_rendering import _load_and_render_prompt, _render_prompt
from .token_management import (
    _check_context_feasibility,
    _constrained_tokens,
    _resolve_max_tokens,
)
from .types import ModelCallResult, RenderedPrompt

if TYPE_CHECKING:
    from ...schemas import PromptConfig, StepConfig

logger = get_logger(__name__)


class BaseHandler(AbstractStepHandler):
    """
    Base class with common handler utilities.

    Extends AbstractStepHandler with helper methods for model invocation,
    prompt building, and generation parameter filtering.

    Subclasses MUST still set `step_type` and implement `execute()`.

    Utilities Provided:
    -------------------
    - `_render_prompt()` - Load + render prompt pair (recommended for most handlers)
    - `_call_model()` - Invoke model via ProxyClient, returns ModelCallResult
    - `_build_generation_params()` - Filter and merge generation parameters
    - `_prompt_builder` - Low-level PromptBuilder (for custom rendering needs)

    Model Alias Resolution:
    -----------------------
    `_call_model()` automatically resolves model aliases via the pipeline
    registry. Handlers can pass either:
    - Short aliases: "phi", "qwen", "llama_3_1_8b"
    - Full IDs: "phi-3-5-mini-instruct-q4-k-m-32768-cpu"

    Resolution order: domain-specific → root → passthrough
    Configure aliases in:
    - pipelines.local/models.yaml (root namespace)
    - pipelines.local/{domain}/models.yaml (domain-specific)

    Concurrency Safety:
    -------------------
    `_call_model()` returns ModelCallResult (immutable value object).
    No shared mutable state - safe for concurrent execution (e.g., map iterations).
    """

    # Class-level aliases to module constants (preserved for subclass access)
    ALLOWED_GENERATION_PARAMS = ALLOWED_GENERATION_PARAMS
    FULL_ID_INDICATORS = FULL_ID_INDICATORS

    def __init__(self) -> None:
        self._prompt_builder = get_prompt_builder()

    # ── Prompt rendering ──────────────────────────────────────────────────────

    def _load_and_render_prompt(
        self,
        prompt_ref: str,
        template_context: dict[str, Any],
        context: PipelineContext,
        *,
        safe: bool = True,
    ) -> tuple[str, PromptConfig]:
        """Load prompt config from registry and render the template string.

        Lower-level than _render_prompt: returns the raw rendered string plus
        the PromptConfig so callers can inspect system_prompt, json_schema, etc.
        Use _render_prompt instead when you only need the ready-to-use prompt pair.
        See prompt_rendering._load_and_render_prompt for full contract.
        """
        return _load_and_render_prompt(
            self._prompt_builder, prompt_ref, template_context, context, safe=safe
        )

    def _render_prompt(
        self,
        prompt_ref: str,
        template_context: dict[str, Any],
        context: PipelineContext,
        *,
        safe: bool = True,
    ) -> RenderedPrompt:
        """Load prompt and return a ready-to-use (system_prompt, user_prompt) pair.

        Preferred over _load_and_render_prompt for handlers that pass the result
        directly to _call_model — avoids re-accessing PromptConfig fields at the
        call site. See prompt_rendering._render_prompt for full contract.
        """
        return _render_prompt(
            self._prompt_builder, prompt_ref, template_context, context, safe=safe
        )

    # ── Domain field helpers ──────────────────────────────────────────────────

    def _require_domain_field(self, step: StepConfig, key: str) -> str:
        """Get a required string config from step domain fields (model_extra).

        Raises:
            ValueError: If the field is missing or empty.
        """
        value = step.get_domain_field(key, "")
        if not value:
            raise ValueError(f"Step '{step.id}' missing '{key}' in step config")
        return value

    def _resolve_input(
        self,
        resolver: Any,
        step: Any,
        field_name: str,
        handler_inputs: dict[str, Any],
    ) -> Any:
        """Resolve a handler_inputs binding to its concrete value via the step resolver.

        handler_inputs bindings may reference outputs from prior steps, context
        variables, or static values. traverse_path handles nested field access
        (e.g., step_output.items[0].text) so handlers don't need to know the
        binding structure.
        """
        from ...execution.resolver import traverse_path

        binding = handler_inputs.get(field_name)
        if not binding:
            raise ValueError(
                f"Step '{step.id}' missing '{field_name}' in handler_inputs"
            )
        return traverse_path(
            resolver.resolve(binding),
            binding.field_path,
            step_name=step.id,
            field_name=field_name,
            binding_repr=str(binding),
            resolver=resolver,
        )

    # ── Token management ──────────────────────────────────────────────────────

    def _resolve_max_tokens(
        self,
        step: StepConfig,
        context: PipelineContext,
        *,
        handler_default: int | None = None,
    ) -> int | None:
        """Resolve max_tokens from token_defaults + constrained_multiplier."""
        return _resolve_max_tokens(step, context, handler_default=handler_default)

    def _constrained_tokens(
        self,
        base: int,
        context: PipelineContext,
    ) -> int:
        """Apply constrained_multiplier for internal sub-calls."""
        return _constrained_tokens(base, context)

    # ── Model resolution ──────────────────────────────────────────────────────

    def _looks_like_full_model_id(self, model_id: str) -> bool:
        """Heuristic to detect if model_id is a full ID vs an alias."""
        return _looks_like_full_model_id(model_id)

    def _resolve_model_pool(
        self,
        step: StepConfig,
        context: PipelineContext,
        *,
        exclude: str | None = None,
    ) -> list[str]:
        """Resolve the step's model_pool to a list of alias strings.

        Used by multi-model handlers (map steps, ensemble verification) to get
        the candidate set. The exclude parameter removes the originator model
        so it isn't re-called as its own verifier.
        See model_resolution._resolve_model_pool for the full resolution contract.
        """
        return _resolve_model_pool(step, context, exclude=exclude)

    def _resolve_model_alias(
        self,
        model_id: str,
        context: PipelineContext,
    ) -> str:
        """Resolve alias → full model ID (sync path).

        Prefer _resolve_model_alias_async inside execute() — use this only
        when you already have a resolved context and need a
        synchronous lookup (e.g., validation, fallback logic outside async paths).
        See model_resolution._resolve_model_alias for the full contract.
        """
        return _resolve_model_alias(model_id, context)

    def _get_cloud_select_fn(self, context: PipelineContext) -> Any:
        """Return the cloud model-selection callable, or None if unavailable.

        See model_resolution._get_cloud_select_fn for the full contract.
        """
        return _get_cloud_select_fn(context)

    def _get_cloud_proxy_mode(self, context: PipelineContext) -> str:
        """Return the cloud proxy transport mode string for observability events.

        See model_resolution._get_cloud_proxy_mode for the full contract.
        """
        return _get_cloud_proxy_mode(context)

    async def _resolve_model_alias_async(
        self,
        model_id: str,
        context: PipelineContext,
        *,
        step_name: str = "",
    ) -> str:
        """Resolve alias → full model ID, with non-blocking cloud ref support.

        The default resolution path for handlers that call models directly
        (rather than going through _call_model). Emits CloudModelResolved /
        CloudModelResolutionFailed events when a cloud:// ref is resolved.
        See model_resolution._resolve_model_alias_async for the full contract.
        """
        return await _resolve_model_alias_async(model_id, context, step_name=step_name)

    def _check_context_feasibility(
        self,
        resolved_model_id: str,
        messages: list[dict[str, str]],
        step: StepConfig,
        context: PipelineContext,
        *,
        system_prompt: str | None = None,
        user_prompt: str = "",
    ) -> None:
        """Pre-flight: does the assembled prompt plausibly fit the model's context?"""
        _check_context_feasibility(
            resolved_model_id,
            messages,
            step,
            context,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            publish_event=self._publish_bus_event,
        )

    # ── Generation params ─────────────────────────────────────────────────────

    def _build_generation_params(
        self,
        step: StepConfig,
        resolved_config: dict[str, Any],
    ) -> tuple[dict[str, Any], set[str]]:
        """Build generation parameters with filtering."""
        return _build_generation_params(step, resolved_config)

    # ── Event bus ─────────────────────────────────────────────────────────────

    def _publish_bus_event(self, context: PipelineContext, event: Any) -> None:
        """Fire-and-forget publish to the global event bus (pipeline-events stream).

        Mirrors DAGExecutor._publish_event(). Used by handlers that need to emit
        bus-level Event objects (not PipelineEvents recorded by the recorder) —
        typically lifecycle signals like StepContextExceeded that other subsystems
        subscribe to. Safe to call from any handler; no-ops if the bus is
        unavailable (tests, offline runs).
        """
        import asyncio

        proxy = getattr(context, "_proxy", None)
        event_bus = getattr(proxy, "event_bus", None) if proxy else None
        if event_bus:
            asyncio.create_task(event_bus.publish_async_nowait(event))

    def _report_progress(
        self,
        step: StepConfig,
        context: PipelineContext,
        *,
        items_total: int,
        items_completed: int,
        models_total: int = 0,
        models_completed: int = 0,
    ) -> None:
        """Emit an in-flight progress event for long-running steps."""
        progress_by_step = getattr(context, "_step_progress_by_step", {})
        progress_by_step[step.name] = {
            "items_total": items_total,
            "items_completed": items_completed,
            "models_total": models_total,
            "models_completed": models_completed,
        }
        setattr(context, "_step_progress_by_step", progress_by_step)

        recorder = context.recorder
        if not recorder:
            return

        from ...events.lifecycle import StepProgress

        recorder.emit(
            StepProgress(
                step_name=step.name,
                items_total=items_total,
                items_completed=items_completed,
                models_total=models_total,
                models_completed=models_completed,
            )
        )

    # ── Core model invocation ─────────────────────────────────────────────────

    async def _call_model(
        self,
        model_id: str,
        prompt: str,
        step: StepConfig,
        context: PipelineContext,
        system_prompt: str | None = None,
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        json_schema: dict[str, Any] | None = None,
        disable_json_response: bool = False,
        call_label: str = "",
        metadata: dict[str, Any] | None = None,
        model_id_is_resolved: bool = False,
        model_profile: str | None = None,
    ) -> ModelCallResult:
        """Invoke model and return complete result. See call_model.py for full docs."""
        return await call_model(
            model_id,
            prompt,
            step,
            context,
            system_prompt,
            temperature=temperature,
            max_tokens=max_tokens,
            json_schema=json_schema,
            disable_json_response=disable_json_response,
            call_label=call_label,
            metadata=metadata,
            model_id_is_resolved=model_id_is_resolved,
            model_profile=model_profile,
            publish_event=self._publish_bus_event,
        )
