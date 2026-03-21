"""
Model invocation events.

Emitted for every LLM call within a pipeline step, capturing the
full request/response cycle for observability and debugging.

Invariant: ∀ _call_model() invocation ⟹ ∃! ModelInvocation event
"""
# ruff: noqa: E501

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field as dataclass_field
from typing import Any

from .base import PipelineEvent


@dataclass(slots=True, kw_only=True)
class ModelInvocationStarted(PipelineEvent):
    """Emitted immediately before an LLM call is attempted.

    Provides a live observability boundary for in-flight model attempts so the
    pipeline viewer can show which model is currently being called before a
    response or failure has been recorded.
    """

    call_label: str = ""
    system_prompt: str | None = None
    user_prompt: str = ""
    request_body: dict[str, Any] | None = None
    metadata: dict[str, Any] | None = None


@dataclass(slots=True, kw_only=True)
class ModelInvocation(PipelineEvent):
    """Emitted for every LLM call within a pipeline step.

    Captures the full request context so failures can be traced back
    to the exact prompt and model that caused them.

    The snapshot_request_id correlates to files on disk at
    {DATA_DIR}/stargate-request-snapshots/{stage}/{ts}_{id}.json
    where stage ∈ {before, after, response-from-gateway, response-to-client}.
    """

    call_label: str = ""
    snapshot_request_id: str = ""  # ID for correlating to request snapshots on disk.
    system_prompt: str | None = None
    user_prompt: str = ""
    request_body: dict[str, Any] | None = None
    response_text: str | None = None  # The text response received from the model.
    error: str | None = None  # Error message if the invocation failed.
    latency_ms: float = (
        0.0  # Total round-trip latency for the model call in milliseconds.
    )
    inference_ms: float = 0.0  # Actual generation time reported by the model (e.g., llama.cpp timings.predicted_ms).
    prompt_tokens: int = 0  # Number of tokens in the prompt.
    completion_tokens: int = 0  # Number of tokens in the model's completion.
    success: bool = (
        True  # True if the model invocation was successful, False otherwise.
    )
    metadata: dict[str, Any] | None = None


@dataclass(slots=True, kw_only=True)
class ContextExceeded(PipelineEvent):
    """Emitted when estimated prompt tokens exceed the model's context window.

    Pre-flight heuristic check (chars/4) that catches gross mismatches
    before wasting an inference call.  Conservative — overestimates tokens
    for English text, so borderline prompts still pass through.
    """

    estimated_tokens: int = 0  # The estimated number of tokens in the prompt.
    context_length: int = 0  # The maximum context length of the model.
    effective_context_per_slot: int = 0  # The effective context available per slot, considering multi-turn conversations or other factors.
    prompt_chars: int = 0  # The number of characters in the prompt.


@dataclass(slots=True, kw_only=True)
class CloudModelResolved(PipelineEvent):
    """Emitted when a ``cloud:`` model_ref resolves to a concrete model ID."""

    requested_ref: str = ""
    resolved_model_id: str = (
        ""  # The concrete model ID resolved from the requested reference.
    )
    cloud_proxy_mode: str = "uds"
    candidate_count: int = 0  # The number of candidate models found during resolution.


@dataclass(slots=True, kw_only=True)
class CloudModelResolutionFailed(PipelineEvent):
    """Emitted when ``cloud:`` model_ref resolution returns no candidates."""

    requested_ref: str = ""
    cloud_proxy_mode: str = "uds"
    reason: str = ""  # The reason why resolution failed.


@dataclass(slots=True, kw_only=True)
class ModelFallbackResolved(PipelineEvent):
    """Emitted when a fallback model succeeds after the primary model failed.

    The primary model (from model_ref / models.yaml) raised ProxyClientError;
    model_requirements was resolved to find alternatives, and fallback_model
    produced a successful result.
    """

    primary_model: str = ""
    fallback_model: str = ""
    primary_error: str = ""
    fallback_attempt: int = 0


@dataclass(slots=True, kw_only=True)
class StepModelFallbackAttempted(PipelineEvent):
    """Emitted when the executor retries a step with a different model.

    Distinct from ModelFallbackResolved (handler-level, ProxyClientError only).
    This fires at the executor level after the full retry chain exhausts,
    covering timeouts, handler errors, and any other exception type.
    """

    primary_model: str = ""
    fallback_model: str = ""
    primary_error: str = ""
    primary_error_type: str = ""
    fallback_attempt: int = 0
    total_fallbacks: int = (
        0  # The total number of fallback models configured for the step.
    )


@dataclass(slots=True, kw_only=True)
class StepModelFallbackSucceeded(PipelineEvent):
    """Emitted when a step-level fallback model succeeds."""

    primary_model: str = ""
    fallback_model: str = ""
    primary_error: str = ""
    fallback_attempt: int = 0


@dataclass(slots=True, kw_only=True)
class StepModelFallbackExhausted(PipelineEvent):
    """Emitted when all step-level fallback models fail."""

    primary_model: str = ""
    fallback_models_tried: list[str] = dataclass_field(
        default_factory=list
    )  # A list of all fallback models that were attempted.
    primary_error: str = ""
    final_error: str = ""  # The error message from the last failed fallback model.


@dataclass(slots=True, kw_only=True)
class StepModelFallbackSuppressed(PipelineEvent):
    """Emitted when step-level fallback is intentionally not attempted."""

    primary_error_type: str = ""
    suppression_reason: str = (
        ""  # The reason why fallback was suppressed (e.g., 'non_retryable_error').
    )


@dataclass(slots=True, kw_only=True)
class GenerationParamsFiltered(PipelineEvent):
    """Emitted when unsupported generation parameters are removed."""

    removed_keys: list[str] = dataclass_field(
        default_factory=list
    )  # List of parameter keys that were removed.
    allowed_keys: list[str] = dataclass_field(
        default_factory=list
    )  # List of parameter keys that are allowed for the model.
