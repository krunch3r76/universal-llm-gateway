"""Frontier-dispatch admission and completion errors.

Structured errors raised by ``frontier_dispatch_v1`` and its admission checks:
unsupported remote-MCP requests, unknown pipeline options, agent/seat model
mismatches, capability-dispatch knob rejections (G9) and catalog-misses (G13),
empty completions, and tool-loop exhaustion. Each carries a stable
``code`` consumed by ``_normalize_pipeline_exception`` so terminal states map
to structured envelopes rather than collapsing to ``pipeline_execution_failed``,
and serializes via ``to_dict()`` for API responses.
"""

from dataclasses import dataclass

from .pipeline_error import PipelineError


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
    historically (e.g. top-level ``effort: \"high\"`` instead of the canonical
    ``generation_parameters.reasoning_effort``). That class of bug burned
    hours of agent debugging when reasoning levers appeared to be ignored.
    Hard-rejecting unknown keys at admission catches the typo upstream and
    points the caller at the right key or the right tool
    (``team_dispatch`` for role-based consults; ``frontier_dispatch`` for
    direct frontier dispatch).
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
            "For team role consults (skeptic/gatherer/synthesizer/reviewer/artisan "
            "or cursor-* seats) prefer `team_dispatch` — it validates options "
            "against the role contract from Cortex. xAI multi-agent roles get "
            "mcp=False auto-derived. For raw role-free dispatches "
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
    """Raised when a concrete seat conflicts with the caller-supplied model.

    Functional roles are model-agnostic. This error is for concrete
    family/platform seats whose provider or variant constraint cannot be
    satisfied (for example ``grok-api-multi`` with a non-multi-agent model).

    Replaces the bare ``ValueError`` that ``check_agent_model_consistency``
    used to raise so ``_normalize_pipeline_exception`` can extract a
    structured ``code`` rather than collapsing to ``pipeline_execution_failed``.

    ``required_variant`` is ``None`` for provider-family mismatches and
    carries the required substring (e.g. ``\"multi-agent\"``) for variant
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
            f"Seat {self.agent!r} expects provider {self.expected_provider!r}; "
            f"model {self.model!r} resolves to {self.provider!r}. "
            f"Use a {self.expected_provider!r} model for this seat."
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
class EmptyCompletionError(PipelineError):
    """Raised when ``frontier_dispatch_v1`` returns ``content=\"\"`` on the
    non-exhausted branch — silent successful-looking completion with no body.

    Distinct from the exhausted branch (intentional outcome of hitting
    ``max_tool_turns``). This guard fires only when the model genuinely
    returned empty content without exhausting the loop.

    Originally surfaced by Orion execution ``d65c723b`` (Cortex assertion
    7903): ``status: completed`` + ``content: \"\"`` + no error envelope. The
    executor's normal exception path catches this and maps the terminal
    state from ``completed`` → ``failed`` so callers polling via
    ``pipeline(op=\"result\", ...)`` see the structured envelope.
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
class CapabilityKnobRejectedError(PipelineError):
    """Raised at the ``gen_params`` boundary when ``resolve_dispatch`` rejects a knob.

    G9 live-flip.

    The per-model ``CapabilityDispatch`` boundary loudly rejects any unsupported
    declared generation knob (e.g. ``reasoning.effort`` on a model whose surface
    does not accept it), replacing the adapters' prior silent ``logger.warning``
    drop. ``violations`` carries EVERY rejected knob (collect-all, not
    first-fail) so the caller sees the full set in one structured 4xx envelope.

    ``code=capability_knob_rejected`` lets ``_normalize_pipeline_exception``
    surface the structured reject rather than collapsing to the generic
    ``pipeline_execution_failed`` terminal state.
    """

    model: str
    provider: str
    violations: list[dict[str, str]]

    def __str__(self) -> str:
        detail = "; ".join(
            f"{v.get('knob')}: {v.get('reject_code')}" for v in self.violations
        )
        return (
            f"Unsupported dispatch knob(s) rejected for model={self.model!r} "
            f"provider={self.provider!r}: {detail}"
        )

    def to_dict(self) -> dict:
        return {
            "error_type": "CapabilityKnobRejectedError",
            "code": "capability_knob_rejected",
            "retryable": self.retryable,
            "model": self.model,
            "provider": self.provider,
            "violations": self.violations,
        }


@dataclass
class CapabilityCatalogMissError(PipelineError):
    """Raised at the ``gen_params`` boundary on a G13 catalog-miss.

    ``resolve_dispatch`` could not infer the dispatch provider/surface for the
    admitted model at all (provider-uninferable). G13 mandates a structural
    fail-fast here, never a silent default. A within-surface fail-closed ceiling
    (e.g. unknown-claude → 8192) is NOT a catalog-miss and resolves normally.

    ``code=capability_catalog_miss`` surfaces the structured fail-fast via
    ``_normalize_pipeline_exception``.
    """

    model: str
    miss_key: str
    miss_reason: str

    def __str__(self) -> str:
        return (
            f"Dispatch catalog-miss for model={self.model!r} "
            f"(key={self.miss_key!r}): {self.miss_reason}"
        )

    def to_dict(self) -> dict:
        return {
            "error_type": "CapabilityCatalogMissError",
            "code": "capability_catalog_miss",
            "retryable": self.retryable,
            "model": self.model,
            "miss_key": self.miss_key,
            "miss_reason": self.miss_reason,
        }


@dataclass
class FrontierDispatchExhaustedError(PipelineError):
    """Raised when ``frontier_dispatch_v1`` hits the tool-loop ceiling without
    producing caller-visible content.

    Exhaustion is a valid observability state, but returning ``content=\"\"`` as
    an OpenAI-successful chat completion makes clients look hung or silently
    empty. Convert that terminal state to a structured pipeline failure.
    """

    execution_id: str
    agent: str | None
    model: str
    provider: str
    turns_used: int
    tool_calls_made: int
    finish_reason: str | None
    block_reason: str | None
    exhaustion_summary: dict | None = None

    @property
    def retryable(self) -> bool:
        """Caller can continue the chat with the diagnostic as context."""
        return True

    def __str__(self) -> str:
        who = f" agent={self.agent!r}" if self.agent else ""
        return (
            "Frontier dispatch exhausted its tool-loop budget with empty "
            f"content: model={self.model!r} provider={self.provider!r} "
            f"turns_used={self.turns_used} tool_calls_made={self.tool_calls_made}"
            f"{who} finish_reason={self.finish_reason!r} "
            f"block_reason={self.block_reason!r} "
            f"execution_id={self.execution_id}. "
            "Terminal state converted to failed."
        )

    def to_dict(self) -> dict:
        return {
            "error_type": "FrontierDispatchExhaustedError",
            "code": "frontier_dispatch_exhausted",
            "retryable": self.retryable,
            "recoverable": True,
            "execution_id": self.execution_id,
            "agent": self.agent,
            "model": self.model,
            "provider": self.provider,
            "turns_used": self.turns_used,
            "tool_calls_made": self.tool_calls_made,
            "finish_reason": self.finish_reason,
            "block_reason": self.block_reason,
            "exhaustion_summary": self.exhaustion_summary,
        }
