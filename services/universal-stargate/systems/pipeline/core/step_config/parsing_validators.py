"""Free-function bodies for :class:`StepConfig` parsing/normalization validators.

Each function here is the body of a Pydantic ``@field_validator`` or
``@model_validator`` declared on :class:`~.config.StepConfig`. The validators
remain bound on the model class (Pydantic requires the decorator to fire at
class-definition time so the schema is registered correctly); the class methods
delegate to these helpers so the parsing rules live in a focused module
separate from the field declarations.

Behavior is identical to the pre-split implementation: the same shorthand
forms (``"step.field"``, contract dict ``{"from": ..., "type": ...}``,
programmatic dict ``{"namespace": ..., "field_path": ...}``) resolve to the
same :class:`InputBinding` / :class:`OutputBinding` / :class:`OutputDeclaration`
/ :class:`ReadsFrom` objects in the same order.
"""

from __future__ import annotations

from typing import Any

from ..step_types import (
    InputBinding,
    OutputBinding,
    OutputDeclaration,
    ReadsFrom,
)


def reject_map_type(v: str) -> str:
    """Reject ``type='map'`` — map is an execution mode, not a handler type."""
    if v == "map":
        raise ValueError(
            "type='map' is not allowed. MAP is an execution mode, not a "
            "handler type. Use an explicit handler type (e.g., 'type: generate') "
            "plus 'map_over'/'map_inputs' (flat) or 'map_config' (nested)."
        )
    return v


def normalize_map_config(values: dict[str, Any]) -> dict[str, Any]:
    """Normalize flat ``map_over`` / ``map_inputs`` fields into ``map_config``."""
    if values.get("map_config") is not None:
        return values

    if "map_over" in values:
        map_config: dict[str, Any] = {"map_over": values.pop("map_over")}
        if "map_inputs" in values:
            map_config["map_inputs"] = values.pop("map_inputs")
        if "min_success_threshold" in values:
            map_config["min_success_threshold"] = values.pop("min_success_threshold")
        if "fail_fast" in values:
            map_config["fail_fast"] = values.pop("fail_fast")
        if "max_concurrency" in values:
            map_config["max_concurrency"] = values.pop("max_concurrency")
        if "model_pool" in values:
            map_config["model_pool"] = values.pop("model_pool")
        if "selection" in values:
            map_config["selection"] = values.pop("selection")
        if "exclude_self" in values:
            map_config["exclude_self"] = values.pop("exclude_self")
        if "inference_timeout_seconds" in values:
            map_config["inference_timeout_seconds"] = values.pop(
                "inference_timeout_seconds"
            )
        if "model_requirements" in values and "model_requirements" not in map_config:
            map_config["model_requirements"] = values.get("model_requirements")
        values["map_config"] = map_config
    return values


def parse_handler_inputs(v: dict[str, Any]) -> dict[str, InputBinding]:
    """Convert string/dict bindings to :class:`InputBinding` objects.

    Supports three forms:
    - String shorthand: ``"extract.json"`` → ``InputBinding(namespace="step", ...)``
    - Contract dict: ``{"from": "extract.json", "type": "dict"}`` → typed
      ``InputBinding``
    - Programmatic dict: ``{"namespace": "step", "step_name": ..., "field_path": ...}``
    """
    if not isinstance(v, dict):
        raise ValueError("handler_inputs must be a mapping of field name -> binding")
    result = {}
    for key, value in v.items():
        if isinstance(value, str):
            result[key] = InputBinding.parse(value)
        elif isinstance(value, InputBinding):
            result[key] = value
        elif isinstance(value, dict):
            if "from" in value:
                binding = InputBinding.parse(value["from"])
                result[key] = InputBinding(
                    namespace=binding.namespace,
                    step_name=binding.step_name,
                    field_path=binding.field_path,
                    declared_type=value.get("type"),
                )
            else:
                if "namespace" not in value or "field_path" not in value:
                    raise ValueError(
                        f"handler_inputs[{key!r}]: dict format requires "
                        "'namespace' and 'field_path'"
                    )
                result[key] = InputBinding(
                    namespace=value["namespace"],
                    step_name=value.get("step_name"),
                    field_path=value["field_path"],
                    declared_type=value.get("declared_type"),
                )
        else:
            result[key] = value
    return result


def parse_handler_outputs(v: dict[str, Any]) -> dict[str, OutputBinding]:
    """Convert string/dict specs to :class:`OutputBinding` objects."""
    if not isinstance(v, dict):
        raise ValueError(
            "handler_outputs must be a mapping of field name -> binding spec"
        )
    result = {}
    for key, value in v.items():
        if isinstance(value, str):
            input_binding = InputBinding.parse(value)
            result[key] = OutputBinding(binding=input_binding)
        elif isinstance(value, dict):
            binding_val = value.get("binding")
            optional = value.get("optional", False)
            if binding_val is None:
                raise ValueError(
                    f"handler_outputs[{key!r}]: dict format requires 'binding' key"
                )
            if isinstance(binding_val, str):
                input_binding = InputBinding.parse(binding_val)
            elif isinstance(binding_val, dict):
                if "namespace" not in binding_val or "field_path" not in binding_val:
                    raise ValueError(
                        f"handler_outputs[{key!r}]: dict binding requires "
                        "'namespace' and 'field_path'"
                    )
                input_binding = InputBinding(
                    namespace=binding_val["namespace"],
                    step_name=binding_val.get("step_name"),
                    field_path=binding_val["field_path"],
                )
            elif isinstance(binding_val, InputBinding):
                input_binding = binding_val
            else:
                raise ValueError(
                    f"handler_outputs[{key!r}]: binding must be str, dict, or "
                    f"InputBinding, got {type(binding_val).__name__}"
                )
            result[key] = OutputBinding(binding=input_binding, optional=optional)
        elif isinstance(value, OutputBinding):
            result[key] = value
        else:
            result[key] = value
    return result


def parse_output_declarations(
    v: dict[str, Any],
) -> dict[str, OutputDeclaration]:
    """Convert dict specs to :class:`OutputDeclaration` objects."""
    if not isinstance(v, dict):
        raise ValueError(
            "output_declarations must be a mapping of output name -> declaration"
        )
    result = {}
    for key, value in v.items():
        if isinstance(value, OutputDeclaration):
            result[key] = value
        elif isinstance(value, dict):
            if "binding" not in value or "type" not in value:
                raise ValueError(
                    f"output_declarations[{key!r}]: dict format requires "
                    "'binding' and 'type'"
                )
            result[key] = OutputDeclaration(
                binding=value["binding"],
                declared_type=value["type"],
                description=value.get("description", ""),
            )
        else:
            raise ValueError(
                f"output_declarations[{key!r}]: expected dict with "
                f"'binding' and 'type', got {type(value).__name__}"
            )
    return result


def parse_reads_from(v: list[Any]) -> list[ReadsFrom]:
    """Convert dict specs to :class:`ReadsFrom` objects."""
    if not isinstance(v, list):
        raise ValueError("reads_from must be a list of dict declarations")
    result = []
    for item in v:
        if isinstance(item, ReadsFrom):
            result.append(item)
        elif isinstance(item, dict):
            if "step" not in item:
                raise ValueError("reads_from item requires 'step'")
            fields = item.get("fields", [])
            result.append(
                ReadsFrom(
                    step=item["step"],
                    fields=tuple(fields),
                    description=item.get("description", ""),
                )
            )
        else:
            raise ValueError(
                f"reads_from: expected dict with 'step' and 'fields', "
                f"got {type(item).__name__}"
            )
    return result
