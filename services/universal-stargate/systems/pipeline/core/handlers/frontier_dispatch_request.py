"""Request assembly helpers for ``frontier_dispatch_v1``.

Extracted from ``frontier_dispatch.py`` to keep that module under the SLOC
ceiling.  Contains:

- ``_VALID_REASONING_EFFORTS`` — accepted effort vocabulary (union of
  documented provider surfaces; provider gating lives in the adapters).
- ``_REASONING_EFFORT_BUDGET_TOKENS`` — budget-mode (pre-adaptive Anthropic) map.
- ``_ANTHROPIC_ADAPTIVE_MODELS`` — Anthropic models that take adaptive
  thinking (per docs/thirdparty/claude-api/upstream/adaptive-thinking.md).
- ``translate_reasoning_effort`` — maps ``reasoning_effort`` to provider-native
  thinking dict.
- ``resolve_model`` / ``resolve_agent`` / ``resolve_user_prompt`` /
  ``resolve_system_prompt`` — parameter extraction helpers shared by ``execute``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from agent_seat import normalize_agent_slug
from agent_seat.registry import resolve_agent_model

from ..execution.resolver import NamespaceResolver, traverse_path

if TYPE_CHECKING:
    from ..schemas import StepConfig
    from .protocol import PipelineContext

# Provider-native ``thinking`` shapes for the convenience ``reasoning_effort``
# knob on ``frontier_dispatch`` / ``/api/v1/frontier/dispatch``.
#
# - Anthropic adaptive-capable models (Mythos Preview, Opus 4.8, Opus 4.7,
#   Opus 4.6, Sonnet 4.6) get ``{"type": "adaptive"}``; effort is surfaced separately
#   via ``req.effort`` → ``output_config.effort`` in the adapter. Per
#   adaptive-thinking.md, manual ``{type: enabled, budget_tokens}`` is
#   deprecated on the 4.6 family and rejected on Opus 4.7.
# - Budget-mode (pre-adaptive Anthropic) models (Sonnet 3.7, Sonnet 4.5, Opus 4.5,
#   etc.) take ``{"type": "enabled", "budget_tokens": N}`` with N drawn from
#   the budget-tokens map. Extended-vocabulary efforts (none/minimal/xhigh/
#   max) have no documented budget mapping and skip thinking on legacy.
# - OpenAI / xAI / OpenRouter / Google: lowercase effort string consumed by
#   the adapter (Responses API ``reasoning.effort`` for OpenAI/xAI; Gemini
#   ``thinkingLevel``/``thinkingBudget`` translation for Google). Provider
#   gating (grok-4 built-in reasoning, gpt-4o non-reasoning, etc.) lives in
#   the adapter layer, not here.

_VALID_REASONING_EFFORTS: frozenset[str] = frozenset(
    # Union of documented provider vocabularies across the four upstream
    # mirrors. Provider-specific support is enforced at the adapter layer.
    {"none", "minimal", "low", "medium", "high", "xhigh", "max"}
)

_REASONING_EFFORT_BUDGET_TOKENS: dict[str, int] = {
    # Used only by the budget-mode (pre-adaptive Anthropic) branch. Adaptive-capable
    # models surface effort via ``output_config.effort`` separately; this
    # map is intentionally narrow to the documented budget-mode vocabulary.
    "low": 2048,
    "medium": 8192,
    "high": 24000,
}

_ANTHROPIC_ADAPTIVE_MODELS: tuple[str, ...] = (
    # Per docs/thirdparty/claude-api/upstream/adaptive-thinking.md. Mythos
    # Preview defaults to adaptive whenever ``thinking`` is unset; Opus 4.8 and
    # Opus 4.7 accept only adaptive (manual ``{type: enabled, budget_tokens}``
    # returns 400 — verified on 4-8 via execution e08ae9a7, "thinking.type.enabled
    # is not supported for this model. Use thinking.type.adaptive"); Opus 4.6
    # and Sonnet 4.6 accept either, but ``enabled+budget_tokens`` is
    # deprecated on those models.
    "claude-mythos-preview",
    "claude-opus-4-8",
    "claude-opus-4-7",
    "claude-opus-4-6",
    "claude-sonnet-4-6",
)


# Models for which ``reasoning_effort="high"`` is the implicit default when
# the caller does not specify one. Provider defaults underserve these models
# in measured comparisons — most concretely, ``xai/grok-4.3`` at provider
# default produced a Phase 1 modularization plan with 4 dedup items vs gpt-5.5
# @ high's 9 items (cortex thread 1024, 2026-05-17); the same dispatch re-run
# at ``reasoning_effort="high"`` reached parity in 3× less time and 5× fewer
# tokens. Centralized here so every *_dispatch path (frontier_dispatch,
# team_dispatch, the raw pipeline escape hatch) inherits the default
# uniformly.
#
# Caller-supplied ``reasoning_effort`` (including the empty-string convention
# from the MCP tool wrapper) is treated identically to the existing
# translation gate: only an absent or empty value triggers the model default.
# Explicit values always win.
_DEFAULT_HIGH_EFFORT_MODELS: frozenset[str] = frozenset(
    {
        "xai/grok-4.3",
    }
)


def resolve_default_reasoning_effort(model: str | None) -> str | None:
    """Return the implicit default ``reasoning_effort`` for ``model``, or None.

    Used by ``frontier_dispatch_v1`` to apply a model-specific default when
    the caller has not supplied one. Returning ``None`` means no default
    applies and the existing provider-native default takes over.
    """
    if not model:
        return None
    if model in _DEFAULT_HIGH_EFFORT_MODELS:
        return "high"
    return None


def _anthropic_uses_adaptive_thinking(model: str | None) -> bool:
    """Return true when Anthropic prefers (or requires) adaptive thinking."""
    if not model:
        return False
    normalized = model.lower()
    return any(m in normalized for m in _ANTHROPIC_ADAPTIVE_MODELS)


def translate_reasoning_effort(
    effort: str, provider: str, *, model: str | None = None
) -> dict[str, Any] | None:
    """Map ``reasoning_effort`` to a provider-native ``thinking`` dict."""
    normalized = effort.strip().lower()
    if normalized not in _VALID_REASONING_EFFORTS:
        raise ValueError(
            f"reasoning_effort={effort!r} must be one of: "
            f"{', '.join(sorted(_VALID_REASONING_EFFORTS))}"
        )
    if provider == "anthropic":
        if _anthropic_uses_adaptive_thinking(model):
            return {"type": "adaptive"}
        budget = _REASONING_EFFORT_BUDGET_TOKENS.get(normalized)
        if budget is None:
            # Extended-vocabulary efforts (none/minimal/xhigh/max) have no
            # documented budget-mode mapping on legacy Anthropic models.
            # Skip thinking config; caller's raw effort still flows via
            # req.effort but the adapter has nothing to surface.
            return None
        return {"type": "enabled", "budget_tokens": budget}
    return {"effort": normalized}


async def resolve_model(
    opts: dict[str, Any],
    step: StepConfig,
    context: PipelineContext,
    *,
    agent: str | None = None,
) -> str:
    """Resolve target model id for this dispatch.

    Precedence:
      1. ``pipeline_options.model`` — caller-supplied runtime override.
      2. ``step.model`` (domain field) — raw model id pinned in YAML
         (e.g. ``model: openai/gpt-5.4-mini``). Bypasses the model-registry
         alias indirection — useful for one-off agent-seat variants where
         a side-car ``models.yaml`` would be overkill.
      3. ``step.get_target_model_id_async`` — StepConfig substrate
         (``step.model_ref`` registry alias OR ``step.model_requirements``
         matched via ``/v1/models/select``).
      4. ``agent``'s registered ``default_model`` — mirrors the
         admission-path fallback in ``build_dispatch_body`` so virtual-model
         agent-seat pipelines (``agent: orion`` step-field, no
         ``pipeline_options``) need not hard-code a model.
    """
    runtime_model = opts.get("model")
    if isinstance(runtime_model, str):
        runtime_model = runtime_model.strip()
        if runtime_model and runtime_model != "default":
            return runtime_model

    step_model = step.get_domain_field("model")
    if isinstance(step_model, str):
        step_model = step_model.strip()
        if step_model and step_model != "default":
            return step_model

    resolved = await step.get_target_model_id_async(
        context._registry,
        domain=context.pipeline.domain,
        search_path=context.pipeline.source_search_path,
        context=context,
    )
    if resolved and resolved != "default":
        return resolved

    agent_resolution_error: str | None = None
    if agent:
        try:
            return resolve_agent_model(agent)
        except ValueError as exc:
            agent_resolution_error = str(exc)

    detail = (
        f"Step '{step.id}': frontier_dispatch_v1 could not resolve a "
        "model. Provide one of: pipeline_options.model "
        "(e.g. 'openai/gpt-5.4'), step.model_ref, "
        "step.model_requirements (matched via /v1/models/select), "
        "or set step.agent to an agent with a registered default_model."
    )
    if agent_resolution_error:
        detail = f"{detail} Agent '{agent}' resolution failed: {agent_resolution_error}"
    raise ValueError(detail)


def resolve_agent(opts: dict[str, Any], step: StepConfig) -> str | None:
    """Resolve role-based dispatch identity.

    Phase 5: reads ``pipeline_options.role`` (the post-migration key) instead
    of ``pipeline_options.agent``. The function name is preserved for blast-
    radius reasons; the *return value* is a normalized role slug used for
    Cortex ``role:{slug}`` resolution downstream.

    Precedence: ``pipeline_options.role`` > step domain field ``role`` > None.

    Uses normalize_agent_slug (handles case, Oppie/Oppia, hyphen/underscore
    variants) so natural references work with the registry alias chain.
    """
    role = opts.get("role") or step.get_domain_field("role")
    if role is None:
        return None
    return normalize_agent_slug(str(role)) or None


def resolve_user_prompt(step: StepConfig, context: PipelineContext) -> str:
    """Resolve the user prompt from step input binding or pipeline source text."""
    binding = step.handler_inputs.get("text")
    if binding is None:
        return context.source_text
    resolver = NamespaceResolver(context)
    value = traverse_path(
        resolver.resolve(binding),
        binding.field_path,
        step_name=step.id,
        field_name="text",
        binding_repr=str(binding),
        resolver=resolver,
    )
    if isinstance(value, str):
        return value
    if value is None:
        return context.source_text
    return str(value)


def _validate_text_only_messages(
    messages: list[Any], *, step_id: str
) -> list[dict[str, Any]]:
    """Validate that the message list contains only plain-text entries.

    Rejects:
    - Any message where 'content' is not a string (rejecting block-lists).
    - Any message with role == 'tool' or anything other than 'user',
      'assistant', 'system'.
    - Any message containing 'tool_calls' or legacy 'function_call'.
    """
    allowed_roles = {"user", "assistant", "system"}
    validated = []
    for idx, msg in enumerate(messages):
        if not isinstance(msg, dict):
            raise ValueError(
                f"Step '{step_id}': message at index {idx} must be a dict, "
                f"got {type(msg).__name__}"
            )
        role = msg.get("role")
        if role not in allowed_roles:
            raise ValueError(
                f"Step '{step_id}': invalid role '{role}' at index {idx}. "
                f"Only {allowed_roles} are allowed in text-only messages."
            )
        if "tool_calls" in msg:
            raise ValueError(
                f"Step '{step_id}': message at index {idx} contains tool calls "
                f"(forbidden in text-only)"
            )
        if "function_call" in msg:
            raise ValueError(
                f"Step '{step_id}': message at index {idx} contains legacy "
                f"function_call (forbidden in text-only)"
            )

        content = msg.get("content")
        if content is not None and not isinstance(content, str):
            raise ValueError(
                f"Step '{step_id}': content at index {idx} must be a plain string, "
                f"got {type(content).__name__}"
            )

        # Ensure only safe keys are kept
        safe_msg: dict[str, Any] = {"role": role}
        if content is not None:
            safe_msg["content"] = content
        validated.append(safe_msg)
    return validated


def resolve_messages(
    step: StepConfig, context: PipelineContext, *, user_prompt: str
) -> list[dict[str, Any]]:
    """Resolve the wire message list for the FrontierRequest.

    Three modes:

    - Mode 1 (explicit assembled-prefix binding): resolves list[dict] from
      ``handler_inputs.messages``, validates text-only structure, and appends
      user_prompt.

    - Mode 2 (``pass_messages: true`` step domain field): forwards the full
      ``context.messages`` list verbatim, filtered to drop ``role="system"``
      entries (system content is conveyed via ``FrontierRequest.system``,
      assembled separately upstream — duplicating it inside ``messages``
      would double-bill the persona on every turn).

      Falls back to the single-prompt list if ``context.messages`` is
      empty or absent — keeps behaviour defined for misrouted invocations.

    - Mode 3 (single-prompt): wraps ``user_prompt`` in a single
      ``[{"role": "user", "content": ...}]``. Matches the historical
      behaviour used by ``team_dispatch`` / ``frontier_dispatch`` admission
      and by all consult-style pipelines that bind one prompt per step.

    The opt-in form is intended for synchronous virtual-model pipelines
    (e.g. agent-seat surfaces exposed via ``/v1/chat/completions``) where
    the caller is a generic OpenAI client sending real conversation
    history. ¬change for any pipeline that does not set the field.
    """
    # Mode 1: explicit assembled-prefix binding via handler_inputs.messages
    if "messages" in step.handler_inputs:
        binding = step.handler_inputs["messages"]
        resolver = NamespaceResolver(context)
        prefix = traverse_path(
            resolver.resolve(binding),
            binding.field_path,
            step_name=step.id,
            field_name="messages",
            binding_repr=str(binding),
            resolver=resolver,
        )
        if not isinstance(prefix, list):
            raise ValueError(
                f"Step '{step.id}': handler_inputs.messages must resolve to "
                f"list[dict], got {type(prefix).__name__}"
            )
        validated = _validate_text_only_messages(prefix, step_id=step.id)
        return validated + [{"role": "user", "content": user_prompt}]

    # Mode 2: existing pass_messages behavior — verbatim client forwarding
    if bool(step.get_domain_field("pass_messages")):
        raw = context.messages or []
        forwarded: list[dict[str, Any]] = [
            m for m in raw if isinstance(m, dict) and m.get("role") != "system"
        ]
        if not forwarded:
            return [{"role": "user", "content": user_prompt}]
        return forwarded

    # Mode 3: default single-prompt
    return [{"role": "user", "content": user_prompt}]


def resolve_system_prompt(step: StepConfig, context: PipelineContext) -> str:
    """Persona-free system prompt.

    Precedence: ``pipeline_options.system`` > ``step.system_prompt`` >
    first system message in ``context.messages``. ``pipeline_options.system``
    carries the caller-supplied prompt for both ``team_dispatch`` and
    ``frontier_dispatch`` MCP dispatches; for persona dispatches the Stargate
    endpoint also auto-assembles birth + briefing + continuation upstream.
    """
    opt_system = context.options.get("system")
    if isinstance(opt_system, str) and opt_system:
        return opt_system
    if step.system_prompt:
        return step.system_prompt
    msgs = context.messages or []
    for m in msgs:
        if isinstance(m, dict) and m.get("role") == "system":
            content = m.get("content", "")
            if isinstance(content, str):
                return content
    return ""
