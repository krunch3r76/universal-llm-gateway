"""Pipeline error hierarchy with structured serialization.

∀ error: error.to_dict() → JSON-compatible dict for API responses
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .map_reduce.iteration_state import IterationResult


class PipelineError(RuntimeError, ABC):
    """Base class for pipeline validation/runtime errors."""

    @property
    def retryable(self) -> bool:
        """Whether this error represents a transient condition worth retrying."""
        return False

    @abstractmethod
    def to_dict(self) -> dict:
        """Serialize to JSON-compatible dict."""
        ...


@dataclass
class BindingResolutionError(PipelineError):
    """Raised when an input binding cannot be resolved."""

    step_name: str
    field_name: str
    binding_repr: str  # String repr of binding
    reason: str

    def __str__(self) -> str:
        return (
            f"[Step '{self.step_name}'] Cannot resolve input '{self.field_name}'\n"
            f"  Binding: {self.binding_repr}\n"
            f"  Reason: {self.reason}"
        )

    def to_dict(self) -> dict:
        return {
            "error_type": "BindingResolutionError",
            "retryable": self.retryable,
            "step_name": self.step_name,
            "field_name": self.field_name,
            "binding": self.binding_repr,
            "reason": self.reason,
        }


@dataclass
class OutputValidationError(PipelineError):
    """Raised when handler output doesn't match declared outputs."""

    step_name: str
    declared_outputs: list[str]
    actual_keys: list[str]
    missing_keys: list[str]

    def __str__(self) -> str:
        return (
            f"[Step '{self.step_name}'] Handler output validation failed\n"
            f"  Declared: {self.declared_outputs}\n"
            f"  Missing: {self.missing_keys}\n"
            f"  Available: {self.actual_keys}"
        )

    def to_dict(self) -> dict:
        return {
            "error_type": "OutputValidationError",
            "retryable": self.retryable,
            "step_name": self.step_name,
            "declared_outputs": self.declared_outputs,
            "actual_keys": self.actual_keys,
            "missing_keys": self.missing_keys,
        }


@dataclass
class InputTypeMismatchError(PipelineError):
    """Raised when resolved value doesn't match handler's input_type."""

    step_name: str
    field_name: str
    expected_type: str
    actual_type: str
    value_preview: str

    def __str__(self) -> str:
        preview = (
            self.value_preview[:100] + "..."
            if len(self.value_preview) > 100
            else self.value_preview
        )
        return (
            f"[Step '{self.step_name}'] Type mismatch for input '{self.field_name}'\n"
            f"  Expected: {self.expected_type}\n"
            f"  Got: {self.actual_type}\n"
            f"  Value: {preview}"
        )

    def to_dict(self) -> dict:
        return {
            "error_type": "InputTypeMismatchError",
            "retryable": self.retryable,
            "step_name": self.step_name,
            "field_name": self.field_name,
            "expected_type": self.expected_type,
            "actual_type": self.actual_type,
            "value_preview": self.value_preview[:100],
        }


@dataclass
class InvalidNamespaceError(PipelineError):
    """Raised when a namespace is used in invalid context."""

    namespace: str
    context: str
    hint: str = ""

    def __str__(self) -> str:
        msg = f"Invalid namespace '{self.namespace}' in context: {self.context}"
        if self.hint:
            msg += f"\n  Hint: {self.hint}"
        return msg

    def to_dict(self) -> dict:
        return {
            "error_type": "InvalidNamespaceError",
            "retryable": self.retryable,
            "namespace": self.namespace,
            "context": self.context,
            "hint": self.hint,
        }


@dataclass
class StepTimeoutError(PipelineError):
    """Raised when entire step (including retries) exceeds timeout."""

    step_name: str
    timeout_seconds: float
    prompt_tokens: int = 0
    completion_tokens: int = 0
    model_call_count: int = 0
    items_total: int | None = None
    items_completed: int | None = None

    @property
    def retryable(self) -> bool:
        return True

    def __str__(self) -> str:
        message = (
            f"[Step '{self.step_name}'] "
            f"Exceeded total timeout of {self.timeout_seconds}s"
        )
        has_progress = (
            self.prompt_tokens > 0
            or self.completion_tokens > 0
            or self.model_call_count > 0
            or self.items_total is not None
            or self.items_completed is not None
        )
        if not has_progress:
            return message

        progress_parts: list[str] = []
        if self.items_total is not None and self.items_completed is not None:
            progress_parts.append(
                f"{self.items_completed}/{self.items_total} claims verified",
            )
        elif self.items_total is not None:
            progress_parts.append(f"{self.items_total} claims tracked")

        progress_parts.append(f"{self.model_call_count} model calls attempted")
        progress_parts.append(
            f"{self.prompt_tokens + self.completion_tokens} tokens used",
        )
        return f"{message}\n  Progress: {', '.join(progress_parts)}"

    def to_dict(self) -> dict:
        return {
            "error_type": "StepTimeoutError",
            "retryable": self.retryable,
            "step_name": self.step_name,
            "timeout_seconds": self.timeout_seconds,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "model_call_count": self.model_call_count,
            "items_total": self.items_total,
            "items_completed": self.items_completed,
        }


@dataclass
class HandlerTimeoutError(PipelineError):
    """Raised when single handler execution exceeds timeout."""

    step_name: str
    timeout_seconds: float
    attempt: int = 1

    @property
    def retryable(self) -> bool:
        return True

    def __str__(self) -> str:
        return (
            f"[Step '{self.step_name}'] Handler execution exceeded timeout of "
            f"{self.timeout_seconds}s (attempt {self.attempt})"
        )

    def to_dict(self) -> dict:
        return {
            "error_type": "HandlerTimeoutError",
            "retryable": self.retryable,
            "step_name": self.step_name,
            "timeout_seconds": self.timeout_seconds,
            "attempt": self.attempt,
        }


@dataclass
class RemoteMcpUnsupportedError(PipelineError):
    """Raised by ``frontier_dispatch_v1`` when the requested ``remote_mcp``
    value is incompatible with the resolved provider's current capability.

    Enforcement lives in the step handler so direct pipeline dispatches (not
    just MCP ``frontier_dispatch``) are validated. Capability matrix
    (∀ provider ∉ the handler's remote-MCP allowlist: ``remote_mcp=True`` is
    rejected; ∀ provider: ``remote_mcp=False`` is accepted):

    - ``anthropic``: either value is allowed; the default is ``True`` iff
      ``mcp`` is enabled. ``remote_mcp=True`` follows the native
      ``mcp_toolset`` path.
    - ``openai`` / ``google`` / ``xai``: ``remote_mcp=True`` is rejected.
      Only providers in the handler's remote-MCP allowlist expose a native
      remote-MCP toolset.
    """

    step_name: str
    provider: str
    model: str
    agent: str | None
    requested: bool
    reason: str

    def __str__(self) -> str:
        who = f" agent={self.agent!r}" if self.agent else ""
        return (
            f"[Step '{self.step_name}'] remote_mcp={self.requested} unsupported "
            f"for provider={self.provider!r} model={self.model!r}{who}: "
            f"{self.reason}"
        )

    def to_dict(self) -> dict:
        return {
            "error_type": "RemoteMcpUnsupportedError",
            "code": "remote_mcp_unsupported",
            "retryable": self.retryable,
            "step_name": self.step_name,
            "provider": self.provider,
            "model": self.model,
            "agent": self.agent,
            "requested": self.requested,
            "reason": self.reason,
        }


@dataclass
class UnknownPipelineOptionsError(PipelineError):
    """Raised by ``frontier_dispatch_v1`` when the caller supplies
    ``pipeline_options`` keys outside the handler's accepted set.

    The raw ``frontier-dispatch`` path silently dropped unrecognized keys
    historically (e.g. top-level ``effort: "high"`` instead of the canonical
    ``generation_parameters.reasoning_effort``). That class of bug burned
    hours of agent debugging when reasoning levers appeared to be ignored.
    Hard-rejecting unknown keys at admission catches the typo upstream and
    points the caller at the right key or the right tool
    (``team_dispatch`` for persona consults; ``frontier_dispatch`` for
    persona-free raw engine).
    """

    step_name: str
    unknown_keys: list[str]
    accepted_keys: list[str]
    agent: str | None

    def __str__(self) -> str:
        who = f" agent={self.agent!r}" if self.agent else ""
        return (
            f"[Step '{self.step_name}']{who} unknown pipeline_options "
            f"keys: {self.unknown_keys}. Accepted: {self.accepted_keys}. "
            "For persona consults (oppie/orion/bard/api_claude/forge/cursor-*) prefer "
            "`team_dispatch` — it validates options against the persona's "
            "contract from Cortex ai_agent entity (MCP access granted to all "
            "except Oppie/Oppia multi-agent). For raw persona-free dispatches "
            "prefer `frontier_dispatch`."
        )

    def to_dict(self) -> dict:
        return {
            "error_type": "UnknownPipelineOptionsError",
            "code": "unknown_pipeline_options",
            "retryable": self.retryable,
            "step_name": self.step_name,
            "unknown_keys": self.unknown_keys,
            "accepted_keys": self.accepted_keys,
            "agent": self.agent,
        }


@dataclass
class AgentModelMismatchError(PipelineError):
    """Raised when the caller-supplied model's provider conflicts with the
    agent's identity-bound provider family, or when the model fails the
    agent's variant requirement (e.g. oppie requires a multi-agent xAI model).

    Replaces the bare ``ValueError`` that ``check_agent_model_consistency``
    used to raise so ``_normalize_pipeline_exception`` can extract a
    structured ``code`` rather than collapsing to ``pipeline_execution_failed``.

    ``required_variant`` is ``None`` for provider-family mismatches and
    carries the required substring (e.g. ``"multi-agent"``) for variant
    mismatches. ``expected_provider`` always carries the bare provider name.
    """

    agent: str
    model: str
    provider: str
    expected_provider: str
    required_variant: str | None = None

    def __str__(self) -> str:
        if self.required_variant:
            return (
                f"Agent {self.agent!r} expects a {self.expected_provider!r} model "
                f"containing {self.required_variant!r}; got {self.model!r}. "
                f"Non-conforming models may reject client-side tools at the API level."
            )
        return (
            f"Agent {self.agent!r} expects provider {self.expected_provider!r}; "
            f"model {self.model!r} resolves to {self.provider!r}. "
            f"Use a {self.expected_provider!r} model for {self.agent!r} dispatches."
        )

    def to_dict(self) -> dict:
        return {
            "error_type": "AgentModelMismatchError",
            "code": "agent_model_mismatch",
            "retryable": self.retryable,
            "agent": self.agent,
            "model": self.model,
            "provider": self.provider,
            "expected_provider": self.expected_provider,
            "required_variant": self.required_variant,
        }


@dataclass
class BootProviderMismatchError(PipelineError):
    """Raised for genuine (boot_mode, provider) misconfigurations that infra
    cannot silently coerce. Primary case (xAI multi-agent + mcp=True) now uses
    silent ``PipelineFrontierDispatchToolSuppressed`` + coercion per
    todo:retire-tools-allowlist-as-caller-concern; this error kept for other
    unresolvable mismatches.

    Note: prompt-layer CORTEX_TOOL_QUICKREF suppression now handled in
    resolve_dispatch_tool_set via include_cortex_quickref param based on
    effective tools/mcp.
    """

    agent: str
    provider: str
    boot_mode: str
    reason: str

    def __str__(self) -> str:
        return (
            f"Agent {self.agent!r} (provider={self.provider!r}, "
            f"boot={self.boot_mode!r}) advertises a client-side MCP tool "
            f"surface the provider cannot execute: {self.reason}"
        )

    def to_dict(self) -> dict:
        return {
            "error_type": "BootProviderMismatchError",
            "code": "boot_provider_mismatch",
            "retryable": self.retryable,
            "agent": self.agent,
            "provider": self.provider,
            "boot_mode": self.boot_mode,
            "reason": self.reason,
        }


@dataclass
class EmptyCompletionError(PipelineError):
    """Raised when ``frontier_dispatch_v1`` returns ``content=""`` on the
    non-exhausted branch — silent successful-looking completion with no body.

    Distinct from the exhausted branch (intentional outcome of hitting
    ``max_tool_turns``). This guard fires only when the model genuinely
    returned empty content without exhausting the loop.

    Originally surfaced by Orion execution ``d65c723b`` (Cortex assertion
    7903): ``status: completed`` + ``content: ""`` + no error envelope. The
    executor's normal exception path catches this and maps the terminal
    state from ``completed`` → ``failed`` so callers polling via
    ``pipeline(op="result", ...)`` see the structured envelope.
    """

    execution_id: str
    agent: str | None
    model: str
    provider: str
    turns_used: int
    finish_reason: str | None

    def __str__(self) -> str:
        who = f" agent={self.agent!r}" if self.agent else ""
        return (
            f"Frontier dispatch returned empty content: model={self.model!r} "
            f"provider={self.provider!r} turns_used={self.turns_used}{who} "
            f"finish_reason={self.finish_reason!r} "
            f"execution_id={self.execution_id}. "
            "Terminal state converted to failed."
        )

    def to_dict(self) -> dict:
        return {
            "error_type": "EmptyCompletionError",
            "code": "empty_completion",
            "retryable": self.retryable,
            "execution_id": self.execution_id,
            "agent": self.agent,
            "model": self.model,
            "provider": self.provider,
            "turns_used": self.turns_used,
            "finish_reason": self.finish_reason,
        }


@dataclass
class MapPartialFailureError(PipelineError):
    """Raised when map step completes with partial success below threshold.

    Contains per-iteration details for debugging:
    - Model and gateway routing
    - Status (completed/timeout/failed)
    - Duration for completed iterations
    - Error messages for failed iterations
    """

    @property
    def retryable(self) -> bool:
        return True

    step_name: str
    completed_count: int
    failed_count: int
    total_count: int
    threshold: int | float
    timeout_seconds: float | None
    iteration_results: tuple["IterationResult", ...]  # Ordered by index
    gateway_serialization: tuple[str, ...] | None = (
        None  # Gateways with multiple iterations
    )

    # Backward compat - computed from iteration_results
    @property
    def failed_indices(self) -> tuple[int, ...]:
        """Indices of non-completed iterations."""
        from .map_reduce.iteration_state import IterationStatus

        return tuple(
            r.index
            for r in self.iteration_results
            if r.status != IterationStatus.COMPLETED
        )

    def __str__(self) -> str:
        threshold_str = (
            f"{self.threshold * 100:.0f}%"
            if isinstance(self.threshold, float)
            else f"{self.threshold}"
        )

        lines = [
            f"Map step '{self.step_name}' did not meet success threshold: "
            f"{self.completed_count}/{self.total_count} succeeded "
            f"(required: {threshold_str})",
        ]

        # Add per-iteration details
        for result in self.iteration_results:
            lines.append(f"  {result.format_line(self.timeout_seconds)}")

        # Add serialization warning if detected
        if self.gateway_serialization:
            gateways = ", ".join(self.gateway_serialization)
            lines.append(f"⚠️ Gateway serialization detected: {gateways}")

        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "error_type": "MapPartialFailureError",
            "retryable": self.retryable,
            "step_name": self.step_name,
            "completed_count": self.completed_count,
            "failed_count": self.failed_count,
            "total_count": self.total_count,
            "threshold": self.threshold,
            "timeout_seconds": self.timeout_seconds,
            "iteration_results": [r.to_dict() for r in self.iteration_results],
            "failed_indices": list(self.failed_indices),
            "gateway_serialization": (
                list(self.gateway_serialization) if self.gateway_serialization else None
            ),
        }
