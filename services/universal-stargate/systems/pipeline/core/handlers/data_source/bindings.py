"""Handler-input binding resolution for ``data_source_v1`` runners.

Resolves a named binding declared in ``step.handler_inputs`` (currently the
optional ``params`` binding for ``sqlite_query``) into a concrete value, using
the pipeline's ``NamespaceResolver`` to locate the binding root and
``traverse_path`` to walk the declared field path. ``traverse_path`` is imported
lazily inside the function to preserve the monolith's import-time semantics and
avoid pulling the resolver internals at module load.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ...execution.resolver import NamespaceResolver
    from ...schemas import StepConfig


def resolve_binding(resolver: NamespaceResolver, step: StepConfig, name: str) -> Any:
    """Resolves a binding from `step.handler_inputs` using the provided resolver.

    Args:
        resolver: The `NamespaceResolver` to resolve the binding.
        step: The `StepConfig` containing the handler inputs.
        name: The name of the binding field to resolve (e.g., 'params').

    Returns:
        The resolved value of the binding.

    Raises:
        ValueError: If the binding is missing or resolution fails.
    """
    from ...execution.resolver import traverse_path

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
