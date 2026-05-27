"""
Resolve ``step.avoid_models_from`` binding paths to model IDs to exclude.

The binding path takes the form ``<step_ref>[.<field_path>]`` and is resolved
against the pipeline context's ``NamespaceResolver`` (namespace=``step``).
The resolved value is normalized to a list of model ID strings; falsy or
unresolvable bindings return an empty list (with a warning logged) so the
auto-resolution branch in ``model_resolution.resolve_primary_model`` can
proceed without the exclusion rather than fail.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from universal_logging import get_logger

if TYPE_CHECKING:
    from ...execution.resolver import NamespaceResolver

logger = get_logger(__name__)


def _resolve_avoid_models(
    binding_path: str,
    resolver: NamespaceResolver,
    step_name: str,
) -> list[str]:
    """Resolve avoid_models_from binding path to model IDs to exclude."""
    parts = binding_path.split(".", 1)
    step_ref = parts[0]
    field_path = parts[1] if len(parts) > 1 else "model_id"

    from ...execution.resolver import traverse_path
    from ...schemas import InputBinding

    binding = InputBinding(
        namespace="step",
        step_name=step_ref,
        field_path=field_path,
    )
    try:
        root = resolver.resolve(binding)
        value = traverse_path(
            root,
            field_path,
            step_name=step_name,
            field_name="avoid_models_from",
            binding_repr=binding_path,
            resolver=resolver,
        )
    except (KeyError, AttributeError, ValueError) as exc:
        logger.warning(
            "[%s] Failed resolving avoid_models_from=%s: %s",
            step_name,
            binding_path,
            exc,
        )
        return []

    if not value:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(v) for v in value if v]
    return []
