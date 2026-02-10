"""
Input formatting for pipeline execution summaries.

Handles resolution and markdown formatting of handler inputs for debugging.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from universal_logging import get_logger

if TYPE_CHECKING:
    from .core.handlers.protocol import PipelineContext
    from .core.schemas import StepConfig

logger = get_logger(__name__)


def format_value_for_display(
    value: Any, max_items: int = 20, max_str_len: int = 1000
) -> tuple[str, str]:
    """
    Format a value for display with truncation.

    Args:
        value: The value to format
        max_items: Maximum list/dict items to show
        max_str_len: Maximum string length before truncation

    Returns:
        (type_description, formatted_value)
    """
    if value is None:
        return "None", "*N/A*"

    if isinstance(value, bool):
        return "bool", f"`{value}`"

    if isinstance(value, int | float):
        return type(value).__name__, f"`{value}`"

    if isinstance(value, str):
        type_desc = f"str ({len(value)} chars)"
        if len(value) > max_str_len:
            truncated = value[:max_str_len] + "..."
            return type_desc, f"```\n{truncated}\n```\n*(truncated)*"
        return type_desc, f"```\n{value}\n```"

    if isinstance(value, list):
        count = len(value)
        type_desc = f"list[{count} items]"

        if count == 0:
            return type_desc, "*empty list*"

        # Check if list of strings (common case)
        if all(isinstance(item, str) for item in value):
            lines = []
            for i, item in enumerate(value[:max_items], 1):
                # Truncate individual items
                display = item[:200] + "..." if len(item) > 200 else item
                lines.append(f"{i}. {display}")
            if count > max_items:
                lines.append(f"\n*... ({count - max_items} more items)*")
            return type_desc, "\n".join(lines)

        # Mixed or complex list
        try:
            formatted = json.dumps(value[:max_items], indent=2, ensure_ascii=False)
            suffix = (
                f"\n*... ({count - max_items} more items)*" if count > max_items else ""
            )
            return type_desc, f"```json\n{formatted}\n```{suffix}"
        except (TypeError, ValueError):
            return type_desc, f"*{count} items (not JSON serializable)*"

    if isinstance(value, dict):
        count = len(value)
        type_desc = f"dict[{count} keys]"

        if count == 0:
            return type_desc, "*empty dict*"

        try:
            # Truncate to max_items keys
            if count > max_items:
                truncated_dict = dict(list(value.items())[:max_items])
                formatted = json.dumps(truncated_dict, indent=2, ensure_ascii=False)
                remaining = count - max_items
                return (
                    type_desc,
                    f"```json\n{formatted}\n```\n*... ({remaining} more keys)*",
                )
            formatted = json.dumps(value, indent=2, ensure_ascii=False)
            return type_desc, f"```json\n{formatted}\n```"
        except (TypeError, ValueError):
            return type_desc, f"*{count} keys (not JSON serializable)*"

    # Fallback for other types
    type_desc = type(value).__name__
    try:
        repr_str = repr(value)[:500]
        return type_desc, f"`{repr_str}`"
    except Exception:
        return type_desc, "*[unable to display]*"


def resolve_handler_inputs(
    spec: StepConfig, context: PipelineContext
) -> dict[str, tuple[str, Any]]:
    """
    Resolve handler_inputs bindings to actual values.

    Args:
        spec: StepConfig with handler_inputs
        context: Pipeline context for resolution

    Returns:
        Dict mapping field_name -> (source_ref, resolved_value)
    """
    from .core.execution.resolver import NamespaceResolver, traverse_path

    if not spec or not getattr(spec, "handler_inputs", None):
        return {}

    resolved = {}
    resolver = NamespaceResolver(context)

    for field_name, binding in spec.handler_inputs.items():
        source_ref = str(binding)
        try:
            root = resolver.resolve(binding)
            value = traverse_path(
                root,
                binding.field_path,
                step_name=spec.name,
                field_name=field_name,
                binding_repr=source_ref,
            )
            resolved[field_name] = (source_ref, value)
        except Exception as e:
            logger.warning(
                f"Could not resolve handler input '{field_name}' "
                f"for step '{spec.name}' from binding '{source_ref}': {e}"
            )
            resolved[field_name] = (source_ref, f"[error: {e}]")

    return resolved


def format_handler_inputs_section(
    spec: StepConfig, context: PipelineContext
) -> list[str]:
    """
    Format handler inputs as markdown section.

    Args:
        spec: StepConfig with handler_inputs
        context: Pipeline context for resolution

    Returns:
        List of markdown lines (empty if no inputs)
    """
    resolved = resolve_handler_inputs(spec, context)
    if not resolved:
        return []

    lines = ["## Handler Inputs", ""]

    for field_name, (source_ref, value) in resolved.items():
        type_desc, formatted = format_value_for_display(value)

        lines.extend(
            [
                f"### {field_name}",
                f"**Source**: `{source_ref}`",
                f"**Type**: {type_desc}",
                "",
                "**Value**:",
                formatted,
                "",
            ]
        )

    return lines


def format_map_iteration_inputs(
    spec: StepConfig,
    iteration_index: int,
    iteration_value: Any,
    iteration_key: str | None,
) -> list[str]:
    """
    Format per-iteration map inputs as markdown.

    Args:
        spec: StepConfig with map_config
        iteration_index: Current iteration index (0-based)
        iteration_value: Current iteration value
        iteration_key: Current iteration key (for dict iteration)

    Returns:
        List of markdown lines (empty if no map inputs)
    """
    map_config = getattr(spec, "map_config", None)
    if not map_config or not isinstance(map_config, dict):
        return []

    # Parse map_config if it's a dict
    try:
        if isinstance(map_config, dict):
            from .core.execution.map_reduce.config import parse_map_config

            parsed = parse_map_config(map_config)
            map_inputs = parsed.map_inputs if parsed else {}
        else:
            map_inputs = getattr(map_config, "map_inputs", {})
    except Exception as e:
        logger.debug(f"Could not parse map_config for iteration inputs: {e}")
        return []

    if not map_inputs:
        return []

    lines = ["**Iteration-specific inputs:**", ""]

    # Show iteration context
    lines.append(f"- **Index**: {iteration_index}")
    if iteration_key is not None:
        lines.append(f"- **Key**: `{iteration_key}`")

    # Format value from map_over
    type_desc, formatted = format_value_for_display(iteration_value)
    lines.extend([f"- **Value** ({type_desc}):", formatted, ""])

    # Show map_inputs bindings (these reference mapNs.iteration.*)
    if map_inputs:
        lines.append("**Map inputs:**")
        for field_name, binding in map_inputs.items():
            lines.append(f"- `{field_name}`: `{binding}`")
        lines.append("")

    return lines
