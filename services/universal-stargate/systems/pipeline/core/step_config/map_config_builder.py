"""Build a typed :class:`MapConfig` from the raw ``map_config`` dict on a step.

The map-config field stays as a dict on :class:`StepConfig` so that
``extra="allow"`` semantics work for domain YAML fragments; this module
materializes the typed object on demand when an executor needs it. The
parsing is structural only — it does not change scheduling semantics.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..step_types import InputBinding, MapConfig

if TYPE_CHECKING:
    from .config import StepConfig


def _parse_binding_dict(
    data: dict[str, Any], field_name: str
) -> dict[str, InputBinding]:
    """Coerce a dict of string-or-binding values to :class:`InputBinding`."""
    parsed_data = {}
    for key, value in data.items():
        if isinstance(value, str):
            parsed_data[key] = InputBinding.parse(value)
        elif isinstance(value, InputBinding):
            parsed_data[key] = value
        else:
            raise TypeError(
                f"{field_name}[{key!r}]: expected str or InputBinding, "
                f"got {type(value).__name__}"
            )
    return parsed_data


def build_map_config(step: StepConfig) -> MapConfig | None:
    """Parse the raw ``map_config`` dict on ``step`` to a :class:`MapConfig`."""
    if not step.map_config:
        return None

    raw = step.map_config
    if not isinstance(raw, dict):
        raise TypeError(
            f"map_config must be a dict when present, got {type(raw).__name__}"
        )

    map_over = _parse_binding_dict(raw.get("map_over", {}), "map_over")
    map_inputs = _parse_binding_dict(raw.get("map_inputs", {}), "map_inputs")

    model_pool_val = raw.get("model_pool")
    if model_pool_val is None:
        model_pool: InputBinding | None = None
    elif isinstance(model_pool_val, str):
        model_pool = InputBinding.parse(model_pool_val)
    elif isinstance(model_pool_val, InputBinding):
        model_pool = model_pool_val
    else:
        raise TypeError(
            f"model_pool: expected str, InputBinding, or None, "
            f"got {type(model_pool_val).__name__}"
        )

    timeout_seconds = raw.get("timeout_seconds")
    if timeout_seconds is None:
        timeout_seconds = step.timeout_seconds

    inference_timeout = raw.get("inference_timeout_seconds")
    kwargs: dict[str, Any] = {
        "max_concurrency": int(raw["max_concurrency"])
        if raw.get("max_concurrency") is not None
        else None,
        "inference_timeout_seconds": float(inference_timeout)
        if inference_timeout is not None
        else None,
    }
    for key in ("max_concurrency", "inference_timeout_seconds"):
        if kwargs[key] is None:
            del kwargs[key]

    return MapConfig(
        map_over=map_over,
        map_inputs=map_inputs,
        timeout_seconds=timeout_seconds,
        min_success_threshold=raw.get("min_success_threshold"),
        fail_fast=raw.get("fail_fast", False),
        model_pool=model_pool,
        model_requirements=raw.get("model_requirements"),
        exclude_self=raw.get("exclude_self", False),
        selection=raw.get("selection", "rotate"),
        **kwargs,
    )
