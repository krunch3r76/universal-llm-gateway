"""Input capture for per-step handler bindings.

Resolves each binding declared in ``step.handler_inputs`` through the
namespace resolver, walks any nested ``field_path``, truncates long string
values to 2000 chars (with a length suffix) to bound recorder payload size,
and records ``{source, value}`` per input. Resolution failures are caught
per-input and logged as warnings so a single bad binding does not abort
input capture for the whole step.

The resolver and ``traverse_path`` are imported lazily inside the function
body to break a potential circular import between the executor package and
the resolver module (preserved verbatim from the prior monolith).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from universal_logging import get_logger

if TYPE_CHECKING:
    from ....schemas import StepConfig
    from .step_observability import StepObservability

logger = get_logger(__name__)


def capture_step_inputs(obs: StepObservability, step: StepConfig) -> dict[str, Any]:
    """Capture resolved handler inputs for observability."""
    from ...resolver import NamespaceResolver, traverse_path

    inputs: dict[str, Any] = {}
    if not step.handler_inputs:
        return inputs
    resolver = NamespaceResolver(obs._executor.context)
    for input_name, binding in step.handler_inputs.items():
        try:
            root = resolver.resolve(binding)
            value = (
                traverse_path(
                    root,
                    binding.field_path,
                    step_name=step.id,
                    field_name=input_name,
                    binding_repr=str(binding),
                    resolver=resolver,
                )
                if binding.field_path
                else root
            )
            if isinstance(value, str) and len(value) > 2000:
                value = value[:2000] + f"... ({len(value)} chars total)"
            inputs[input_name] = {
                "source": str(binding),
                "value": value,
            }
        except Exception as e:
            logger.warning(
                "Failed to capture input '%s' for step '%s' (binding=%s): %s",
                input_name,
                step.id,
                binding,
                e,
            )
            inputs[input_name] = {"source": str(binding), "value": None}
    return inputs
