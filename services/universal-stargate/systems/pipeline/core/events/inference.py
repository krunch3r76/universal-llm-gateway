"""
Model invocation events.

Emitted for every LLM call within a pipeline step, capturing the
full request/response cycle for observability and debugging.

Invariant: ∀ _call_model() invocation ⟹ ∃! ModelInvocation event
"""

from __future__ import annotations

from dataclasses import dataclass
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
