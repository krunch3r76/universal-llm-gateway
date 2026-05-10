"""Request assembly helpers for ``frontier_dispatch_v1``.

Extracted from ``frontier_dispatch.py`` to keep that module under the SLOC
ceiling.  Contains:

- ``_REASONING_EFFORT_BUDGET_TOKENS`` — provider-native thinking budget map.
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
# - Anthropic: ``budget_tokens`` (max_tokens auto-bumped by the adapter).
#   Numbers parallel Google's 2.5 budget map for predictable behavior.
# - OpenAI / xAI / OpenRouter: lowercase string consumed by the Responses
#   API as ``reasoning.effort``. grok-4 strips it adapter-side (built-in
#   reasoning, no effort control) — observable via INFO log.
# - Google: uppercase string; the adapter normalizes via ``.upper()`` and
#   maps to ``thinkingBudget`` (2.5) or ``thinkingLevel`` (3.x).
_REASONING_EFFORT_BUDGET_TOKENS: dict[str, int] = {
    "low": 2048,
    "medium": 8192,
    "high": 24000,
}


def translate_reasoning_effort(effort: str, provider: str) -> dict[str, Any] | None:
    """Map ``reasoning_effort`` to a provider-native ``thinking`` dict."""
    normalized = effort.strip().lower()
    if normalized not in _REASONING_EFFORT_BUDGET_TOKENS:
        raise ValueError(
            f"reasoning_effort={effort!r} must be one of: low, medium, high"
        )
    if provider == "anthropic":
        budget = _REASONING_EFFORT_BUDGET_TOKENS[normalized]
        return {"type": "enabled", "budget_tokens": budget}
    if provider == "google":
        return {"effort": normalized.upper()}
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

    if agent:
        try:
            return resolve_agent_model(agent)
        except ValueError:
            pass

    raise ValueError(
        f"Step '{step.id}': frontier_dispatch_v1 could not resolve a "
        "model. Provide one of: pipeline_options.model "
        "(e.g. 'openai/gpt-5.4'), step.model_ref, "
        "step.model_requirements (matched via /v1/models/select), "
        "or set step.agent to an agent with a registered default_model."
    )


def resolve_agent(opts: dict[str, Any], step: StepConfig) -> str | None:
    """Resolve agent identity.

    Precedence: ``pipeline_options.agent`` > step domain field > None.

    Uses normalize_agent_slug (handles case, Oppie/Oppia, hyphen/underscore
    variants) so natural references from personas work with registry.
    """
    agent = opts.get("agent") or step.get_domain_field("agent")
    if agent is None:
        return None
    return normalize_agent_slug(str(agent)) or None


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


def resolve_messages(
    step: StepConfig, context: PipelineContext, *, user_prompt: str
) -> list[dict[str, Any]]:
    """Resolve the wire message list for the FrontierRequest.

    Two modes:

    - Default (single-prompt): wraps ``user_prompt`` in a single
      ``[{"role": "user", "content": ...}]``. Matches the historical
      behaviour used by ``team_dispatch`` / ``frontier_dispatch`` admission
      and by all consult-style pipelines that bind one prompt per step.

    - ``pass_messages: true`` (step domain field): forwards the full
      ``context.messages`` list verbatim, filtered to drop ``role="system"``
      entries (system content is conveyed via ``FrontierRequest.system``,
      assembled separately upstream — duplicating it inside ``messages``
      would double-bill the persona on every turn).

      Falls back to the single-prompt list if ``context.messages`` is
      empty or absent — keeps behaviour defined for misrouted invocations.

    The opt-in form is intended for synchronous virtual-model pipelines
    (e.g. agent-seat surfaces exposed via ``/v1/chat/completions``) where
    the caller is a generic OpenAI client sending real conversation
    history. ¬change for any pipeline that does not set the field.
    """
    if not bool(step.get_domain_field("pass_messages")):
        return [{"role": "user", "content": user_prompt}]
    raw = context.messages or []
    forwarded: list[dict[str, Any]] = [
        m for m in raw if isinstance(m, dict) and m.get("role") != "system"
    ]
    if not forwarded:
        return [{"role": "user", "content": user_prompt}]
    return forwarded


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
