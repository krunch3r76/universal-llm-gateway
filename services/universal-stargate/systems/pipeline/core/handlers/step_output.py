"""Step output types for pipeline handler execution."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any


@dataclass
class MapIterationState:
    """
    State for current map iteration.

    Populated by MapExecutor to enable provenance tracking through map steps.
    Handlers can access via context._map_state to determine source step/key.
    """

    source_step_name: str  # e.g., "answer_all"
    iteration_key: str | None  # e.g., "phi" (None for list-based iterations)
    iteration_index: int  # e.g., 0


@dataclass
class StepOutput:
    """
    Output from a single step execution.

    Captures both the result and execution metadata.

    The 'raw' field contains the unprocessed model response.
    The 'json' field contains JSON-parsed response data (when json_schema specified).
    The 'text' property returns the best text representation.

    Optional prompt fields (system_prompt, user_prompt) capture the exact
    prompts sent to the model for debugging and execution summaries.

    For terminal-passthrough streaming steps (see
    ``PipelineSpec.is_stream_passthrough_eligible``), ``stream`` carries an
    async iterator of OpenAI ``chat.completion.chunk`` dicts. When ``stream``
    is set, ``raw`` is ``""`` and ``prompt_tokens`` / ``completion_tokens`` /
    ``latency_ms`` are 0 at handler-return time — the consumer drives the
    iterator and aggregates from the final chunk's ``usage`` field.

    Invariants:
    - ∀ output.json: (json_schema specified) ⟹ (json ∈ dict[str, Any] | None)
    - stream is not None ⟹ raw == "" ∧ prompt_tokens == 0 ∧ completion_tokens == 0
    """

    raw: str
    json: dict[str, Any] | None = None
    json_parse_error: str | None = None  # Why json is null when json_schema was set
    prompt_tokens: int = 0  # Tokens in prompt (system + user messages)
    completion_tokens: int = 0  # Tokens in model response
    latency_ms: float = 0.0
    model_call_count: int = 0  # Number of _call_model() invocations for this step
    model_id: str | None = None
    step_id: str = ""
    error: str | None = None  # Non-None if step failed but produced partial output

    # Prompt capture (optional, for execution summaries and debugging)
    system_prompt: str | None = None  # System message sent to model (if any)
    user_prompt: str | None = None  # User message sent to model

    # Generation parameters (actual values used during execution)
    temperature: float | None = None  # Temperature used for generation
    max_tokens: int | None = None  # Max tokens used for generation

    # Full request body sent to LLM (for complete reproducibility)
    request_body: dict[str, Any] | None = None

    # Streaming-passthrough iterator. When set, the step is a
    # terminal-passthrough-eligible generate step producing chunks for direct
    # SSE forwarding. See PipelineSpec.is_stream_passthrough_eligible. The
    # consumer (lifecycle layer) drives the iterator and aggregates from the
    # final chunk's ``usage`` field. ``raw`` is ``""`` and token counts are
    # 0 at handler-return time; provenance is populated normally because it
    # derives from model_id alone (not from the streamed content).
    stream: AsyncIterator[dict[str, Any]] | None = None

    # Reasoning trace from OpenAI-family reasoning models. Shape is whatever
    # the upstream returned (structured blocks or a flat string). ``None`` for
    # non-reasoning models or providers that don't surface the trace.
    reasoning: Any = None
    # From OpenAI ``usage.completion_tokens_details.reasoning_tokens`` — part
    # of ``completion_tokens``, exposed separately so consumers can distinguish
    # reasoning spend from visible output.
    reasoning_tokens: int = 0

    # Embedded provenance (auto-populated from model_id + step_id)
    provenance: dict[str, Any] | None = None

    def __post_init__(self):
        """Auto-populate provenance from model_id and step_id if not set."""
        if self.provenance is None and self.model_id and self.step_id:
            from provenance import create_provenance

            prov = create_provenance(
                model_id=self.model_id,
                step_id=self.step_id,
            )
            self.provenance = prov.to_dict()

    @property
    def text(self) -> str:
        """
        Get text content, preferring json fields if available.

        Domain handlers populate 'json' with extracted content.
        This property returns the best text representation:
        1. json["translation"] if present (translation domain)
        2. json["text"] if present (generic)
        3. raw content as fallback
        """
        if self.json:
            if "translation" in self.json:
                return self.json["translation"]
            if "text" in self.json:
                return self.json["text"]
        return self.raw

    @property
    def has_content(self) -> bool:
        """Check if output has non-empty content."""
        return bool(self.text.strip())

    def to_checkpoint_data(self) -> tuple[str, dict[str, Any] | None, dict[str, Any]]:
        """
        Extract checkpoint data from output.

        Returns:
            Tuple of (raw_text, json_data, metadata)
        """
        metadata: dict[str, Any] = {
            "latency_ms": self.latency_ms,
            "model_id": self.model_id,
            "step_id": self.step_id,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }
        # Remove None values for cleaner storage
        metadata = {k: v for k, v in metadata.items() if v is not None}

        return self.raw, self.json, metadata
