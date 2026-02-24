"""
Generic builtin step handlers.

These are domain-agnostic handlers that work for any pipeline type.
Domain-specific handlers can override these via domain router.

IMPORTANT: All handlers return StepOutput. They never write to context.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from universal_logging import get_logger

from ..prompts import get_prompt_builder
from .protocol import AbstractStepHandler, PipelineContext, StepOutput
from .registry import register_handler

if TYPE_CHECKING:
    from ..schemas import PromptConfig, StepConfig

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class ModelCallResult:
    """
    Immutable result from a single model invocation.

    Replaces mutable self._last_* fields to enable safe concurrent execution.
    All per-call data returned together, no shared state.
    """

    content: str
    finish_reason: str
    request_body: dict[str, Any]
    prompt_tokens: int
    completion_tokens: int
    map_iteration_request_id: str | None
    snapshot_request_id: str | None
    system_prompt: str | None
    user_prompt: str


@dataclass(slots=True, kw_only=True)
class RenderedPrompt:
    """Ready-to-use prompt pair from PromptBuilder rendering.

    Returned by BaseHandler._render_prompt() — the canonical way to
    load a prompt from the registry and render it with context variables.
    """

    system_prompt: str | None
    user_prompt: str


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

    # Supported generation parameters (whitelist)
    ALLOWED_GENERATION_PARAMS = {
        "temperature",
        "max_tokens",
        "top_p",
        "top_k",
        "stop",
        "response_format",
        "seed",
        "stream",
        "presence_penalty",
        "frequency_penalty",
    }

    # Indicators used to detect full model IDs vs aliases
    FULL_ID_INDICATORS = frozenset(
        {
            "instruct",
            "chat",
            "base",
            "q4",
            "q8",
            "q6",
            "f16",
            "cpu",
            "gpu",
            "hybrid",
            "uncensored",
        }
    )

    def __init__(self):
        self._prompt_builder = get_prompt_builder()
        # NOTE: _last_* fields removed - use ModelCallResult return value instead

    def _load_and_render_prompt(
        self,
        prompt_ref: str,
        template_context: dict[str, Any],
        context: PipelineContext,
        *,
        safe: bool = True,
    ) -> tuple[str, PromptConfig]:
        """
        Load prompt from registry and render with context.

        Unified interface for the common load-then-render pattern.
        Avoids confusion between PromptBuilder (rendering) and
        PipelineRegistry (loading).

        Args:
            prompt_ref: Dotted reference like "consensus.v3.3.verification_serial_math"
            template_context: Variables for template substitution
            context: Pipeline context (for registry access)
            safe: If True, use render_safe (missing vars → ""); otherwise strict

        Returns:
            Tuple of (rendered_prompt, prompt_config)

        Raises:
            KeyError: If prompt_ref not found in registry
            ValueError: If safe=False and template has missing variables

        Example:
            rendered, config = self._load_and_render_prompt(
                "consensus.v3.3.answer",
                {"question": user_question},
                context,
            )
            system_prompt = config.system_prompt
        """
        prompt_config = context._registry.get_prompt(prompt_ref)

        if safe:
            rendered = self._prompt_builder.render_safe(
                prompt_config.template,
                template_context,
            )
        else:
            rendered = self._prompt_builder.render(
                prompt_config.template,
                template_context,
            )

        return rendered, prompt_config

    def _render_prompt(
        self,
        prompt_ref: str,
        template_context: dict[str, Any],
        context: PipelineContext,
        *,
        safe: bool = True,
    ) -> RenderedPrompt:
        """Load prompt from registry, render template via PromptBuilder.

        Canonical way to get a ready-to-use (system_prompt, user_prompt) pair.
        Uses PromptBuilder internally — never Jinja2, never str.format().

        Both system_prompt and template are rendered through PromptBuilder,
        so {placeholder} variables work in either field. System prompt
        rendering always uses render_safe (missing vars → "") since most
        system prompts are static text.

        Args:
            prompt_ref: Prompt reference (e.g., "consensus.v3.3.answer")
            template_context: Variables for {placeholder} substitution
            context: Pipeline context (for registry access)
            safe: If True, missing vars → ""; otherwise raises ValueError

        Returns:
            RenderedPrompt with system_prompt and user_prompt ready for _call_model()
        """
        rendered_template, prompt_config = self._load_and_render_prompt(
            prompt_ref,
            template_context,
            context,
            safe=safe,
        )

        # Render system_prompt through PromptBuilder (always safe — most are static)
        system_prompt = prompt_config.system_prompt
        if system_prompt:
            system_prompt = self._prompt_builder.render_safe(
                system_prompt, template_context
            )

        return RenderedPrompt(
            system_prompt=system_prompt,
            user_prompt=rendered_template,
        )

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
        """Resolve a single handler input binding to its value."""
        from ..execution.resolver import traverse_path

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

    def _resolve_max_tokens(
        self,
        step: StepConfig,
        context: PipelineContext,
        *,
        handler_default: int | None = None,
    ) -> int | None:
        """Resolve max_tokens from token_defaults + constrained_multiplier."""
        from .token_resolution import resolve_max_tokens

        return resolve_max_tokens(step, context, handler_default=handler_default)

    def _constrained_tokens(
        self,
        base: int,
        context: PipelineContext,
    ) -> int:
        """Apply constrained_multiplier for internal sub-calls."""
        from .token_resolution import constrained_tokens

        return constrained_tokens(base, context)

    def _build_generation_params(
        self,
        step: StepConfig,
        resolved_config: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Build generation parameters with filtering.

        Hierarchy:
        1. Start with resolved config (from handler logic, token_defaults, etc.)
        2. Overlay step.generation_parameters (explicit overrides)
        3. Filter to whitelist

        response_format merging from prompt.json_schema preserved for compatibility.

        Filtering: Only ALLOWED_GENERATION_PARAMS passed to ProxyClient.

        Token constraint invariant: max_tokens from resolved_config already includes
        constrained_multiplier when expansion_safe=false, so explicit step values
        must not override it (would bypass epistemic boundedness constraint).
        """
        # Start with resolved config values (handler-calculated, token_defaults, etc.)
        params = {}
        if resolved_config.get("temperature") is not None:
            params["temperature"] = resolved_config["temperature"]
        if resolved_config.get("max_tokens") is not None:
            params["max_tokens"] = resolved_config["max_tokens"]

        # Overlay step's generation_parameters (explicit overrides take precedence)
        # Exclude max_tokens: already resolved with constraint multiplier applied
        step_overrides = {
            k: v for k, v in step.generation_parameters.items() if k != "max_tokens"
        }
        params.update(step_overrides)

        # Special case: merge response_format from prompt.json_schema if not in step
        # (backward compatibility for prompts that specify json_schema)
        if not params.get("response_format") and resolved_config.get("json_schema"):
            params["response_format"] = {
                "type": "json_object",
                "schema": resolved_config["json_schema"],
            }

        # Filter to whitelist
        filtered = {
            k: v for k, v in params.items() if k in self.ALLOWED_GENERATION_PARAMS
        }

        # Warn on filtered params
        removed = set(params.keys()) - set(filtered.keys())
        if removed:
            logger.warning(
                f"Filtered unsupported generation params: {removed}. "
                f"Allowed: {self.ALLOWED_GENERATION_PARAMS}"
            )

        return filtered

    def _looks_like_full_model_id(self, model_id: str) -> bool:
        """
        Heuristic to detect if model_id is a full ID vs an alias.

        Full IDs typically contain:
        - Multiple hyphens (segmented naming)
        - Version indicators (instruct, chat, base)
        - Quantization markers (q4, q6, q8, f16)
        - Context length (8192, 32768, 131072)
        - Deployment markers (cpu, gpu, hybrid)
        - Variant markers (uncensored)

        Design Decision: Models without these indicators (e.g., "hermes-3-llama-3.1-8b")
        will be treated as aliases and fail if not registered. This is intentional —
        unregistered model IDs should fail fast rather than silently pass through
        and fail later at federation routing.

        Examples:
            "phi" → False (alias)
            "phi-3-5-mini-instruct-q4-k-m-32768-cpu" → True (full ID)
            "qwen2-5-7b-instruct-q4-k-m-32768-cpu" → True (full ID)
            "hermes3-llama-3.1-70b-uncensored-16384-hybrid" → True (full ID)
            "hermes-3-llama-3.1-8b" → False (no indicators, treated as alias)
        """
        # Aliases are typically short, single words or underscored
        if "_" in model_id and "-" not in model_id:
            return False  # Underscored aliases like "llama_3_1_8b"

        # Full IDs have multiple segments
        segments = model_id.split("-")
        if len(segments) < 3:
            return False

        # Look for keyword indicators (quantization, deployment, variant)
        if any(seg.lower() in self.FULL_ID_INDICATORS for seg in segments):
            return True

        # Look for numeric context length (4-6 digit numbers: 2048, 8192, 32768, 131072)
        if any(seg.isdigit() and 1000 <= int(seg) <= 999999 for seg in segments):
            return True

        return False

    def _resolve_model_pool(
        self,
        step: StepConfig,
        context: PipelineContext,
        *,
        exclude: str | None = None,
    ) -> list[str]:
        """Resolve model_pool domain field to a list of model aliases.

        Reads step's ``model_pool`` domain field (via model_extra), which may be:
        - list[str]: literal alias list — used directly
        - "optionsNs.KEY" or "KEY": resolved via pipeline options
        - None: returns []

        Args:
            step: Current step config.
            context: Pipeline execution context.
            exclude: Alias to remove from the pool (e.g. the originator model).

        Returns:
            List of resolved model aliases (may be empty).
        """
        pool = step.get_domain_field("model_pool")

        if pool is None:
            return []

        if isinstance(pool, list):
            aliases = list(pool)
        elif isinstance(pool, str):
            key = pool.removeprefix("optionsNs.")
            resolved = (context.options or {}).get(key, [])
            if not isinstance(resolved, list):
                logger.error(
                    "Step '%s': model_pool option '%s' is not a list: %r",
                    step.id,
                    key,
                    resolved,
                )
                return []
            aliases = list(resolved)
        else:
            logger.error("Step '%s': unexpected model_pool type: %r", step.id, pool)
            return []

        if exclude:
            aliases = [a for a in aliases if a != exclude]

        return aliases

    def _resolve_model_alias(
        self,
        model_id: str,
        context: PipelineContext,
    ) -> str:
        """
        Resolve model alias to full ID via registry.

        Resolution order: optionsNs binding → domain-specific → root → passthrough

        Args:
            model_id: Alias (e.g., "phi"), optionsNs binding, or full ID
            context: Pipeline context with registry

        Returns:
            Full model ID

        Raises:
            KeyError: If alias not found and doesn't appear to be full ID
            ValueError: If optionsNs binding references missing/invalid option
        """
        if model_id.startswith("optionsNs."):
            key = model_id[len("optionsNs.") :]
            resolved = (context.options or {}).get(key)
            if not resolved or not isinstance(resolved, str):
                raise ValueError(
                    f"model_ref '{model_id}' references optionsNs.{key} "
                    f"but no string value found in pipeline options"
                )
            model_id = resolved

        registry = context._registry
        domain = context.pipeline.domain

        try:
            model_config = registry.get_model_config(
                model_id,
                domain=domain,
                search_path=context.pipeline.source_search_path,
            )
            resolved = model_config.model
            if resolved != model_id:
                logger.debug(f"Resolved model alias: {model_id} → {resolved}")
            return resolved
        except KeyError:
            # Check if it looks like a full ID (contains version indicators)
            # Full IDs have patterns: name-version-variant-quantization-context
            if self._looks_like_full_model_id(model_id):
                logger.debug(f"Model '{model_id}' not in registry, using as full ID")
                return model_id

            # Alias not found and doesn't look like full ID
            logger.error(
                f"Model alias '{model_id}' not found in registry "
                f"(domain={domain}). Check pipelines.local/models.yaml or "
                f"pipelines.local/{domain}/models.yaml"
            )
            raise

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
    ) -> ModelCallResult:
        """
        Invoke model and return complete result.

        Model aliases are automatically resolved via the pipeline registry.
        Use short aliases (e.g., "phi") or full IDs interchangeably.

        Emits a ModelInvocation event for every call (success or failure)
        so the pipeline viewer can display the full request/response chain.

        Args:
            model_id: Target model identifier (alias or full ID)
            prompt: User prompt content
            step: Step specification for timeout/retry options
            context: Pipeline context with dependencies
            system_prompt: Optional system prompt prepended to messages
            temperature: Generation temperature (None = model default)
            max_tokens: Max tokens (None = model default)
            json_schema: JSON schema for structured output
            disable_json_response: Remove response_format from params
            call_label: Purpose identifier for observability (e.g., "decompose",
                "verify", "classify"). Helps distinguish sub-calls in complex handlers.
            metadata: Optional dict forwarded to ModelInvocation for viewer linkage
                (e.g., claim_ids for verify_batch).

        Returns:
            ModelCallResult with content, request body, tokens,
            and map_iteration_request_id.
            All data is per-call (no instance state mutation).

        Raises:
            ProxyClientError: If model call fails or response cannot be parsed
        """
        import time as _time

        from ..events.inference import ModelInvocation
        from ..execution.proxy_client import ProxyClientError

        # AUTO-RESOLVE model alias to full ID
        resolved_model_id = self._resolve_model_alias(model_id, context)

        # Build messages
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        # Build generation params with filtering
        resolved = {
            "temperature": temperature,
            "max_tokens": max_tokens,
            "json_schema": json_schema,
        }
        params = self._build_generation_params(step, resolved)

        # Explicitly remove response_format for free-form output (e.g., math LaTeX)
        if disable_json_response:
            params.pop("response_format", None)

        # Build complete request body (capture for debugging)
        request_body = {
            "model": resolved_model_id,
            "messages": messages,
            "stream": False,
            **params,
        }

        # Determine HTTP timeout from step config
        http_timeout = None
        if step.handler_timeout_seconds:
            http_timeout = step.handler_timeout_seconds + 30
        elif step.timeout_seconds:
            http_timeout = step.timeout_seconds + 30

        # Resolve skip_token_counting: step overrides pipeline options
        skip_tc = step.skip_token_counting
        if skip_tc is None:
            skip_tc = context.pipeline.options.skip_token_counting

        recorder = context.recorder
        call_start = _time.monotonic()

        # Invoke via Stargate
        client = context.get_proxy_client()
        try:
            (
                response,
                map_iteration_request_id,
                snapshot_request_id,
            ) = await client.chat_completion(
                model=resolved_model_id,
                messages=messages,
                execution_id=context.execution_id,
                step_id=step.id,
                skip_token_counting=skip_tc,
                timeout=http_timeout,
                map_iteration_request_id=context.map_iteration_request_id,
                **params,
            )
        except ProxyClientError as e:
            call_duration_ms = (_time.monotonic() - call_start) * 1000
            e.add_note(f"Pipeline step: {step.id}")
            e.add_note(f"Execution ID: {context.execution_id}")
            e.add_note(f"Model: {resolved_model_id}")
            if resolved_model_id != model_id:
                e.add_note(f"Resolved from alias: {model_id}")
            logger.error(
                f"Model invocation failed: {e.status_code} {e.detail} "
                f"(step={step.id}, model={resolved_model_id})"
            )
            if recorder:
                recorder.emit(
                    ModelInvocation(
                        step_name=step.name,
                        model_id=resolved_model_id,
                        call_label=call_label,
                        system_prompt=system_prompt,
                        user_prompt=prompt,
                        request_body=request_body,
                        error=(
                            f"{e.status_code} {e.detail}"
                            if hasattr(e, "status_code")
                            else str(e)
                        ),
                        latency_ms=call_duration_ms,
                        success=False,
                        metadata=metadata,
                    )
                )
            raise

        # Extract token usage
        usage = response.get("usage", {})
        prompt_tokens = usage.get("prompt_tokens", 0)
        completion_tokens = usage.get("completion_tokens", 0)

        # Extract actual inference duration from llama.cpp timings.
        # predicted_ms = generation time only (excludes queue wait + prompt eval).
        # queue_wait = latency_ms - inference_ms gives the scheduling delay.
        timings = response.get("timings") or {}
        inference_ms = float(timings.get("predicted_ms", 0.0))

        # Extract content and finish_reason with validation
        try:
            choice = response["choices"][0]
            content = choice["message"]["content"]
            finish_reason = choice.get("finish_reason", "unknown")
            if content is None:
                raise ProxyClientError(
                    "Response content is None",
                    status_code=502,
                    detail=response,
                )
        except (KeyError, IndexError, TypeError) as e:
            raise ProxyClientError(
                f"Malformed response from Stargate: {e}",
                status_code=502,
                detail=response,
            ) from e

        # Fail fast on truncation — prevents corrupted output from
        # propagating to downstream steps (e.g., malformed JSON)
        if finish_reason == "length":
            from ..dag import ResponseTruncatedError

            effective_max_tokens = request_body.get("max_tokens")
            raise ResponseTruncatedError(
                step_id=step.id,
                completion_tokens=completion_tokens,
                max_tokens=effective_max_tokens,
                response_preview=content,
            )

        call_duration_ms = (_time.monotonic() - call_start) * 1000

        result = ModelCallResult(
            content=content,
            finish_reason=finish_reason,
            request_body=request_body,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            map_iteration_request_id=map_iteration_request_id,
            snapshot_request_id=snapshot_request_id,
            system_prompt=system_prompt,
            user_prompt=prompt,
        )

        # Emit observability event for every successful call
        if recorder:
            recorder.emit(
                ModelInvocation(
                    step_name=step.name,
                    model_id=resolved_model_id,
                    call_label=call_label,
                    snapshot_request_id=snapshot_request_id or "",
                    system_prompt=system_prompt,
                    user_prompt=prompt,
                    request_body=request_body,
                    response_text=content,
                    latency_ms=call_duration_ms,
                    inference_ms=inference_ms,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    success=True,
                    metadata=metadata,
                )
            )

        # Auto-record for pipeline-level token aggregation
        context.record_model_call(result)

        return result


# Import generate so GenericGenerateHandler is registered and re-exportable.
from . import generate as _generate  # noqa: E402, F401
from .generate import GenericGenerateHandler  # noqa: E402, F401


@register_handler
class SelectWinnerHandler:
    """
    Handler for select_winner steps.

    Domain-agnostic - just selects output from a previous step.
    Returns StepOutput; does NOT write to context.
    """

    step_type = "select_winner"

    async def execute(
        self,
        step: StepConfig,
        context: PipelineContext,
    ) -> StepOutput:
        """Select output from a previous step."""
        source_step = step.from_

        if not source_step:
            raise ValueError(f"select_winner '{step.id}' missing 'from' field")

        source_output = context.get_output(source_step)

        if source_output is None:
            logger.warning(
                f"select_winner: source '{source_step}' not found, "
                f"available: {list(context.outputs.keys())}"
            )
            return StepOutput(raw="")

        logger.info(f"select_winner '{step.id}': selected from '{source_step}'")

        # Return StepOutput - do NOT write to context
        return StepOutput(
            raw=source_output.text,
            model_id=source_output.model_id,
        )

    def validate(self, step: StepConfig) -> list[str]:
        errors = []
        if not step.from_:
            errors.append(f"select_winner '{step.id}' missing 'from' field")
        return errors
