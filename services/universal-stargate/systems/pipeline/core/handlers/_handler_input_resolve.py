"""Shared handler_inputs resolution helpers for archive turn handlers."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..execution.resolver import NamespaceResolver, traverse_path

if TYPE_CHECKING:
    from ..schemas import StepConfig


def _resolve_input(resolver: NamespaceResolver, step: StepConfig, name: str) -> Any:
    """Resolve a required handler_inputs binding to its final value."""
    binding = step.handler_inputs.get(name)
    if binding is None:
        raise ValueError(f"Step '{step.id}' missing handler_inputs.{name}")
    root = resolver.resolve(binding)
    return traverse_path(
        root,
        binding.field_path,
        step_name=step.id,
        field_name=name,
        binding_repr=str(binding),
        resolver=resolver,
    )


def _resolve_required_str(
    resolver: NamespaceResolver, step: StepConfig, name: str
) -> str:
    value = _resolve_input(resolver, step, name)
    if not isinstance(value, str):
        raise TypeError(
            f"Step '{step.id}': handler_inputs.{name} must resolve to str, "
            f"got {type(value).__name__}"
        )
    return value


def _resolve_required_int(
    resolver: NamespaceResolver, step: StepConfig, name: str
) -> int:
    value = _resolve_input(resolver, step, name)
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(
            f"Step '{step.id}': handler_inputs.{name} must resolve to int, "
            f"got {type(value).__name__}"
        )
    return value


def _resolve_optional(
    resolver: NamespaceResolver,
    step: StepConfig,
    name: str,
    *,
    default: Any,
) -> Any:
    """Resolve an optional handler_inputs binding, returning ``default``
    when no binding is provided. Resolution errors still propagate.
    """
    binding = step.handler_inputs.get(name)
    if binding is None:
        return default
    root = resolver.resolve(binding)
    return traverse_path(
        root,
        binding.field_path,
        step_name=step.id,
        field_name=name,
        binding_repr=str(binding),
        resolver=resolver,
    )
