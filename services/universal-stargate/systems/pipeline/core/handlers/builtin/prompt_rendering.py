"""Prompt loading and rendering utilities."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..protocol import PipelineContext
from .types import RenderedPrompt

if TYPE_CHECKING:
    from ...prompts import PromptBuilder
    from ...schemas import PromptConfig


def _load_and_render_prompt(
    prompt_builder: PromptBuilder,
    prompt_ref: str,
    template_context: dict[str, Any],
    context: PipelineContext,
    *,
    safe: bool = True,
) -> tuple[str, PromptConfig]:
    """
    Load prompt from registry and render with context.

    Unified interface for the common load-then-render pattern.
    Avoids confusion between PromptBuilder (rendering) and
    PipelineRegistry (loading).

    Args:
        prompt_builder: PromptBuilder instance for template rendering.
        prompt_ref: Dotted reference like "consensus.v3.3.verification_serial_math"
        template_context: Variables for template substitution
        context: Pipeline context (for registry access)
        safe: If True, use render_safe (missing vars → ""); otherwise strict

    Returns:
        Tuple of (rendered_prompt, prompt_config)

    Raises:
        KeyError: If prompt_ref not found in registry
        ValueError: If safe=False and template has missing variables

    Example:
        rendered, config = _load_and_render_prompt(
            self._prompt_builder,
            "consensus.v3.3.answer",
            {"question": user_question},
            context,
        )
        system_prompt = config.system_prompt
    """
    prompt_config = context._registry.get_prompt(prompt_ref)

    if safe:
        rendered = prompt_builder.render_safe(
            prompt_config.template,
            template_context,
        )
    else:
        rendered = prompt_builder.render(
            prompt_config.template,
            template_context,
        )

    return rendered, prompt_config


def _render_prompt(
    prompt_builder: PromptBuilder,
    prompt_ref: str,
    template_context: dict[str, Any],
    context: PipelineContext,
    *,
    safe: bool = True,
) -> RenderedPrompt:
    """Load prompt from registry, render template via PromptBuilder.

    Canonical way to get a ready-to-use (system_prompt, user_prompt) pair.
    Uses PromptBuilder internally — never Jinja2, never str.format().

    Both system_prompt and template are rendered through PromptBuilder,
    so {placeholder} variables work in either field. System prompt
    rendering always uses render_safe (missing vars → "") since most
    system prompts are static text.

    Args:
        prompt_builder: PromptBuilder instance for template rendering.
        prompt_ref: Prompt reference (e.g., "consensus.v3.3.answer")
        template_context: Variables for {placeholder} substitution
        context: Pipeline context (for registry access)
        safe: If True, missing vars → ""; otherwise raises ValueError

    Returns:
        RenderedPrompt with system_prompt and user_prompt ready for _call_model()
    """
    rendered_template, prompt_config = _load_and_render_prompt(
        prompt_builder,
        prompt_ref,
        template_context,
        context,
        safe=safe,
    )

    # Render system_prompt through PromptBuilder (always safe — most are static)
    system_prompt = prompt_config.system_prompt
    if system_prompt:
        system_prompt = prompt_builder.render_safe(system_prompt, template_context)

    return RenderedPrompt(
        system_prompt=system_prompt,
        user_prompt=rendered_template,
    )
