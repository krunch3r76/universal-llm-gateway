"""Request assembly helpers for ``frontier_dispatch_v1``.

Package-private request assembly helpers. Contains:

- ``_VALID_REASONING_EFFORTS`` — accepted effort vocabulary (union of
  documented provider surfaces; per-model support is enforced at the
  ``CapabilityDispatch`` boundary, G9).
- ``translate_reasoning_effort`` — thin spec-reader delegating to the per-surface
  ``ModelWrapper.translate_reasoning`` (the SOLE translation mechanism).
- ``resolve_default_reasoning_effort`` — thin spec-reader delegating to the
  registry ``default_reasoning_effort``.
- ``resolve_model`` / ``resolve_agent`` / ``resolve_user_prompt`` /
  ``resolve_system_prompt`` — parameter extraction helpers shared by ``execute``.

The per-model reasoning/max-output DATA + translation MECHANISM live in the
``llm_adapters.capability_dispatch`` registry (thread 1234/1271); the static
maps (``_REASONING_EFFORT_BUDGET_TOKENS``, ``_ANTHROPIC_ADAPTIVE_MODELS``,
``_DEFAULT_HIGH_EFFORT_MODELS``) were deleted there.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from agent_seat import normalize_agent_slug
from agent_seat.registry import resolve_agent_model
from llm_adapters.capability_dispatch import default_reasoning_effort, wrapper_for

from ...execution.resolver import NamespaceResolver, traverse_path

if TYPE_CHECKING:
    from ...schemas import StepConfig
    from ..protocol import PipelineContext

# Accepted effort vocabulary (union of documented provider surfaces). Per-model
# support — and the native-shape translation — is owned by the
# ``llm_adapters.capability_dispatch`` registry/wrappers; only the admission
# vocabulary gate stays here.
_VALID_REASONING_EFFORTS: frozenset[str] = frozenset(
    {"none", "minimal", "low", "medium", "high", "xhigh", "max"}
)


def resolve_default_reasoning_effort(model: str | None) -> str | None:
    """Return the implicit default ``reasoning_effort`` for ``model``, or None.

    Thin spec-reader delegating to the registry ``default_reasoning_effort``
    (the reshaped ``_DEFAULT_HIGH_EFFORT_MODELS``). Returning ``None`` means no
    default applies and the provider-native default takes over.
    """
    if not model:
        return None
    return default_reasoning_effort(model)


def translate_reasoning_effort(
    effort: str, provider: str, *, model: str | None = None
) -> dict[str, Any] | None:
    """Map ``reasoning_effort`` to a provider-native ``thinking`` dict.

    Thin spec-reader: validates against the admission vocabulary, then delegates
    to the per-surface ``ModelWrapper.translate_reasoning`` (the SOLE
    translation MECHANISM). ``provider`` is retained for the caller signature;
    the wrapper is keyed on ``model`` (cloud ``ModelId.normalized``).
    """
    normalized = effort.strip().lower()
    if normalized not in _VALID_REASONING_EFFORTS:
        raise ValueError(
            f"reasoning_effort={effort!r} must be one of: "
            f"{', '.join(sorted(_VALID_REASONING_EFFORTS))}"
        )
    if not model:
        return None
    return wrapper_for(model).translate_reasoning(normalized)


def normalize_frontier_wire_model(model: str) -> str:
    """Prefix bare cloud ids (e.g. gpt-5.5) before provider routing.

    Agent substrates (``cursor/``, ``cdp/``) resolve successfully but are
    rejected here — frontier_dispatch is the cloud native-loop path.
    """
    from model_id import (
        require_cloud_api_backend,
        resolve_wire_model_id,
    )

    return require_cloud_api_backend(
        resolve_wire_model_id(model, require_cloud=True),
        capability="frontier_dispatch",
    ).wire_id


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
         agent-seat pipelines (``role: gatherer`` step-field, no
         ``pipeline_options``) need not hard-code a model.
    """
    candidate: str | None = None

    runtime_model = opts.get("model")
    if isinstance(runtime_model, str):
        runtime_model = runtime_model.strip()
        if runtime_model and runtime_model != "default":
            candidate = runtime_model

    if candidate is None:
        step_model = step.get_domain_field("model")
        if isinstance(step_model, str):
            step_model = step_model.strip()
            if step_model and step_model != "default":
                candidate = step_model

    if candidate is None:
        resolved = await step.get_target_model_id_async(
            context._registry,
            domain=context.pipeline.domain,
            search_path=context.pipeline.source_search_path,
            context=context,
        )
        if resolved and resolved != "default":
            candidate = resolved

    agent_resolution_error: str | None = None
    if candidate is None and agent:
        try:
            candidate = resolve_agent_model(agent)
        except ValueError as exc:
            agent_resolution_error = str(exc)

    if candidate is not None:
        return normalize_frontier_wire_model(candidate)

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

    Uses normalize_agent_slug (handles case, hyphen/underscore variants, legacy
    seat slugs) so natural references work with the registry alias chain.
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
      behaviour used by ``team_dispatch`` and ``/api/v1/frontier/dispatch`` admission
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
    carries the caller-supplied prompt for MCP ``team_dispatch`` and internal
    frontier HTTP dispatches; for persona dispatches the Stargate
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
