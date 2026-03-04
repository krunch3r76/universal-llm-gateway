"""
Model invocation events.

Emitted for every LLM call within a pipeline step, capturing the
full request/response cycle for observability and debugging.

Invariant: ∀ _call_model() invocation ⟹ ∃! ModelInvocation event
"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field as dataclass_field
from typing import Any

from .base import PipelineEvent


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
    snapshot_request_id: str = ""
    system_prompt: str | None = None
    user_prompt: str = ""
    request_body: dict[str, Any] | None = None
    response_text: str | None = None
    error: str | None = None
    latency_ms: float = 0.0
    inference_ms: float = 0.0  # llama.cpp timings.predicted_ms: actual generation time
    prompt_tokens: int = 0
    completion_tokens: int = 0
    success: bool = True
    metadata: dict[str, Any] | None = None


@dataclass(slots=True, kw_only=True)
class ContextExceeded(PipelineEvent):
    """Emitted when estimated prompt tokens exceed the model's context window.

    Pre-flight heuristic check (chars/4) that catches gross mismatches
    before wasting an inference call.  Conservative — overestimates tokens
    for English text, so borderline prompts still pass through.
    """

    estimated_tokens: int = 0
    context_length: int = 0
    effective_context_per_slot: int = 0
    prompt_chars: int = 0


@dataclass(slots=True, kw_only=True)
class CloudModelResolved(PipelineEvent):
    """Emitted when a ``cloud:`` model_ref resolves to a concrete model ID."""

    requested_ref: str = ""
    resolved_model_id: str = ""
    cloud_proxy_url: str = ""
    candidate_count: int = 0


@dataclass(slots=True, kw_only=True)
class CloudModelResolutionFailed(PipelineEvent):
    """Emitted when ``cloud:`` model_ref resolution returns no candidates."""

    requested_ref: str = ""
    cloud_proxy_url: str = ""
    reason: str = ""


@dataclass(slots=True, kw_only=True)
class GenerationParamsFiltered(PipelineEvent):
    """Emitted when unsupported generation parameters are removed."""

    removed_keys: list[str] = dataclass_field(default_factory=list)
    allowed_keys: list[str] = dataclass_field(default_factory=list)
