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

from ..execution.resolver import NamespaceResolver, traverse_path

if TYPE_CHECKING:
    from ..schemas import StepConfig
    from .protocol import PipelineContext

# Provider-native ``thinking`` shapes for the convenience ``reasoning_effort``
# knob on ``frontier_generate`` / ``/api/v1/frontier/generate``.
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
) -> str:
    """Resolve target model id for this dispatch.

    Precedence:
      1. ``pipeline_options.model`` — caller-supplied runtime override.
      2. ``step.get_target_model_id_async`` — delegates to the StepConfig
         resolution substrate, which honors ``step.model_ref`` (registry
         lookup or raw model id) AND ``step.model_requirements`` (matched
         via ``/v1/models/select`` through ``get_ranked_candidates``).
    """
    runtime_model = opts.get("model")
    if isinstance(runtime_model, str):
        runtime_model = runtime_model.strip()
        if runtime_model and runtime_model != "default":
            return runtime_model

    resolved = await step.get_target_model_id_async(
        context._registry,
        domain=context.pipeline.domain,
        search_path=context.pipeline.source_search_path,
        context=context,
    )
    if not resolved or resolved == "default":
        raise ValueError(
            f"Step '{step.id}': frontier_dispatch_v1 could not resolve a "
            "model. Provide one of: pipeline_options.model "
            "(e.g. 'openai/gpt-5.4'), step.model_ref, or "
            "step.model_requirements (matched via /v1/models/select)."
        )
    return resolved


def resolve_agent(opts: dict[str, Any], step: StepConfig) -> str | None:
    """Resolve agent identity.

    Precedence: ``pipeline_options.agent`` > step domain field > None.

    Normalizes hyphen-form slugs to underscore-form so callers using
    either ``api-claude`` or ``api_claude`` hit the same registry entry.
    """
    agent = opts.get("agent") or step.get_domain_field("agent")
    if agent is None:
        return None
    agent_str = str(agent).strip().replace("-", "_")
    return agent_str or None


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


def resolve_system_prompt(step: StepConfig, context: PipelineContext) -> str:
    """Persona-free system prompt.

    Precedence: ``pipeline_options.system`` > ``step.system_prompt`` >
    first system message in ``context.messages``. ``pipeline_options.system``
    carries the caller-supplied prompt for both ``team_generate`` and
    ``frontier_generate`` MCP dispatches; for persona dispatches the Stargate
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
