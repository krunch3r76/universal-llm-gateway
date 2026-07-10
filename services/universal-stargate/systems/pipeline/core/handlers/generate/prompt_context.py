"""
Prompt rendering and template-context assembly for the generate handler.

Three free functions; the corresponding class methods on
``GenericGenerateHandler`` are thin delegators that pass injected callables
so subclass override of the documented ``_build_prompt_context`` hook (and,
defensively, ``_format_for_prompt``) propagates through
``self._render_user_prompt``.

- ``format_for_prompt`` — coerce arbitrary Python values (lists, dicts,
  scalars) into prompt-friendly plain text. Lists become numbered or
  comma-separated; dicts become ``key: value`` lines; scalars stringify.

- ``build_prompt_context`` — assemble the prompt template context dict:
  ``text`` + pipeline options + resolved ``handler_inputs`` (through
  ``NamespaceResolver``) + merged ``resolved_map_inputs`` (from
  ``MapExecutor``). Takes a ``format_value`` callable so subclass override
  of ``_format_for_prompt`` remains honored when reached via
  ``self._build_prompt_context``.

- ``render_user_prompt`` — validate template placeholders against the
  built context (raising ``ValueError`` with diagnostic detail on empty,
  missing, or unfilled placeholders) and run
  ``prompt_builder.render_safe``. Placeholders listed in
  ``PromptConfig.optional_placeholders`` are exempt from the non-empty
  check (they must still resolve). Takes ``build_context`` as injected dep
  so domain handler overrides of ``_build_prompt_context`` propagate.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from universal_logging import get_logger

if TYPE_CHECKING:
    from collections.abc import Callable

    from ...schemas import PromptConfig, StepConfig
    from ..protocol import PipelineContext

logger = get_logger(__name__)


def format_for_prompt(value: Any, field_name: str) -> str:
    """
    Format value for prompt template (avoid JSON, use plain text).

    Arrays are formatted as numbered lists for better LLM comprehension.
    Simple string lists (like theme_words) are formatted as comma-separated.
    """
    if isinstance(value, list):
        if not value:
            return "(empty array)"

        if all(isinstance(item, str) and len(item) < 50 for item in value):
            return ", ".join(value)

        lines = []
        for i, item in enumerate(value):
            if isinstance(item, dict):
                parts = [f"{k}: {v}" for k, v in item.items()]
                lines.append(f"{i}: {', '.join(parts)}")
            elif isinstance(item, str):
                lines.append(f"{i}: {item if item.strip() else '(paragraph break)'}")
            else:
                lines.append(f"{i}: {item}")
        return "\n".join(lines)

    if isinstance(value, dict):
        # Format dict as key: value pairs
        return "\n".join(f"{k}: {v}" for k, v in value.items())

    # For simple values (strings, numbers, etc.), return as-is
    return str(value) if value is not None else ""


def build_prompt_context(
    step: StepConfig,
    context: PipelineContext,
    *,
    format_value: Callable[[Any, str], str],
) -> dict[str, Any]:
    """
    Build context dictionary for prompt template rendering.

    Override in domain handlers via the ``_build_prompt_context`` method on
    ``GenericGenerateHandler`` for domain-specific context variables. Base
    implementation provides ``text`` plus all pipeline options, resolves
    ``step.handler_inputs`` through ``NamespaceResolver``, and merges
    ``step.resolved_map_inputs`` for MapExecutor iteration-specific values.

    The ``format_value`` callable (typically ``self._format_for_prompt``) is
    injected so subclass overrides remain honored when reached via the
    ``_build_prompt_context`` class method.

    CRITICAL: Automatically merges step.resolved_map_inputs into prompt context.
    When using map_inputs, fields are available directly in template:

    Example:
        map_inputs:
          prose_text: mapNs.iteration.value.text

        # Automatically available in template as {prose_text}
        template: |
          Reformat: {prose_text}

    Subclasses extending GenericGenerateHandler don't need to manually
    handle template variables - just validate and call super().execute().
    """
    from ...execution.resolver import NamespaceResolver, traverse_path

    prompt_context: dict[str, Any] = {
        "text": context.source_text,
        **context.options,
    }
    # scope_options may be a list in pipeline YAML; prompt template expects string
    if isinstance(prompt_context.get("scope_options"), list):
        prompt_context["scope_options"] = "\n".join(
            f'    "{x}"' for x in prompt_context["scope_options"]
        )

    # Resolve handler_inputs and add to prompt context
    if step.handler_inputs:
        logger.info(
            f"Step '{step.id}': Resolving {len(step.handler_inputs)} handler_inputs"
        )
        resolver = NamespaceResolver(context)
        for field_name, binding in step.handler_inputs.items():
            try:
                logger.debug(
                    f"Step '{step.id}': Resolving handler_input '{field_name}' "
                    f"from binding: {binding}"
                )
                root = resolver.resolve(binding)
                value = traverse_path(
                    root,
                    binding.field_path,
                    step_name=step.id,
                    field_name=field_name,
                    binding_repr=str(binding),
                    resolver=resolver,
                )
                logger.debug(
                    f"Step '{step.id}': Resolved '{field_name}' to type "
                    f"{type(value).__name__}"
                )
                # Format arrays as plain text (models struggle with JSON in prompts)
                formatted_value = format_value(value, field_name)
                prompt_context[field_name] = formatted_value
                logger.info(
                    f"Step '{step.id}': Added '{field_name}' to prompt context "
                    f"({len(str(formatted_value))} chars)"
                )
            except (KeyError, AttributeError, ValueError) as e:
                logger.error(
                    "Step '%s': Failed to resolve handler_input '%s' "
                    "(binding=%s): %s. Input will be absent from prompt context.",
                    step.id,
                    field_name,
                    binding,
                    e,
                    exc_info=True,
                )
    else:
        logger.debug(f"Step '{step.id}': No handler_inputs to resolve")

    # Merge pre-resolved map_inputs (from MapExecutor for iteration-specific values)
    # Dicts are kept as-is so dotted template paths (e.g. {retrieval.chunks})
    # resolve correctly through PromptBuilder._resolve_path.
    if step.resolved_map_inputs:
        for field_name, value in step.resolved_map_inputs.items():
            if isinstance(value, dict):
                prompt_context[field_name] = value
            else:
                prompt_context[field_name] = format_value(value, field_name)
            logger.debug(
                f"Step '{step.id}': Added resolved map_input '{field_name}' "
                f"({len(str(value))} chars)"
            )

    return prompt_context


def render_user_prompt(
    prompt_config: PromptConfig,
    step: StepConfig,
    context: PipelineContext,
    *,
    prompt_builder: Any,
    build_context: Callable[[StepConfig, PipelineContext], dict[str, Any]],
) -> str:
    """
    Render user prompt from template.

    Single responsibility: Prompt rendering.
    Calls ``build_context`` (typically ``self._build_prompt_context``) which
    can be overridden by domain handlers via the corresponding class hook.

    Raises:
        ValueError: If rendered prompt is empty, whitespace-only, or unfilled
    """
    prompt_context = build_context(step, context)

    # Get required placeholders
    required_placeholders = prompt_builder.get_placeholders(prompt_config.template)

    # Validate all required placeholders resolve in context
    # Uses _resolve_path (supports dotted paths like {retrieval.chunks})
    missing_placeholders = prompt_builder.validate_context(
        prompt_config.template, prompt_context
    )
    if missing_placeholders:
        available_keys = list(prompt_context.keys())
        raise ValueError(
            f"Step '{step.id}': Template has unfilled placeholders: "
            f"{sorted(missing_placeholders)}. "
            f"Available context keys: {sorted(available_keys)}. "
            f"Check that handler_inputs are correctly configured and "
            f"dependency steps have completed."
        )

    # Placeholders declared optional on the prompt may resolve to an
    # empty/whitespace-only value (e.g. doc_generate draft's existing_doc on
    # a first run, where no architecture doc exists yet). They must still
    # resolve — unresolvable placeholders were already rejected above.
    optional_placeholders = set(prompt_config.optional_placeholders)
    for placeholder in required_placeholders:
        if placeholder in optional_placeholders:
            continue
        value = prompt_builder._resolve_path(placeholder, prompt_context)
        if value is None or (isinstance(value, str) and not value.strip()):
            raise ValueError(
                f"Step '{step.id}': Required placeholder "
                f"'{placeholder}' is empty or None. "
                f"Value: {repr(value)}. "
                f"Check handler_inputs and dependency steps."
            )

    rendered = prompt_builder.render_safe(prompt_config.template, prompt_context)

    # Validate rendered prompt is not empty
    if not rendered or not rendered.strip():
        available_keys = list(prompt_context.keys())
        raise ValueError(
            f"Step '{step.id}': Rendered user prompt is empty. "
            f"Template requires: {sorted(required_placeholders)}, "
            f"but context has: {sorted(available_keys)}. "
            f"This usually means dependency steps haven't completed or "
            f"placeholders don't match step IDs."
        )
    return rendered
