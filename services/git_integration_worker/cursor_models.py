"""Executor-local cursor model registry and knob validation for cursor-sdk dispatches."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from cursor_capabilities import (
    CURSOR_MODEL_CAPABILITIES,
    canonical_cursor_bare_id,
    catalog_divergences,
    is_cursor_model_denied,
)
from cursor_sdk.types import ModelParameterValue, ModelSelection, SDKModel
from model_id import ModelId
from universal_logging import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class CursorKnobSpec:
    """Knob accepted values; serializes to ``ModelSelection.params`` as ``ModelParameterValue``."""

    name: str
    accepted: tuple[str, ...]
    default: str | None = None


@dataclass(frozen=True, slots=True)
class CursorSdkModelConfig:
    """Trusted cursor-sdk model entry with optional knob specs."""

    model_id: str
    params: tuple[CursorKnobSpec, ...] = ()


class CapabilityDescriptorDrift(Exception):  # noqa: N818
    """Raised when the live Cursor catalog diverges from ``CURSOR_MODEL_CAPABILITIES``."""


def _build_trusted_models() -> dict[str, CursorSdkModelConfig]:
    trusted: dict[str, CursorSdkModelConfig] = {}
    for model_id, capability in CURSOR_MODEL_CAPABILITIES.items():
        params = tuple(
            CursorKnobSpec(
                name=name,
                accepted=spec.accepted,
                default=spec.default,
            )
            for name, spec in capability.knobs.items()
        )
        trusted[model_id] = CursorSdkModelConfig(model_id=model_id, params=params)
    return trusted


_TRUSTED_CURSOR_MODELS: dict[str, CursorSdkModelConfig] = _build_trusted_models()


def project_live_catalog(models: Sequence[SDKModel]) -> dict[str, dict[str, object]]:
    """Project ``Client.list_models()`` into a descriptor-comparable shape."""
    projected: dict[str, dict[str, object]] = {}
    for model in models:
        knobs: dict[str, tuple[str, ...]] = {}
        for param in model.parameters:
            knobs[param.id] = tuple(value.value for value in param.values)
        default_variant: dict[str, str] = {}
        for variant in model.variants:
            if variant.is_default:
                default_variant = {param.id: param.value for param in variant.params}
                break
        projected[model.id] = {
            "knobs": knobs,
            "default_variant": default_variant,
        }
    return projected


def assert_capability_descriptor_fresh(
    *,
    list_models: Callable[[], Sequence[SDKModel]] | None = None,
) -> None:
    """Raise ``CapabilityDescriptorDrift`` when the live catalog diverges."""
    if list_models is None:
        from cursor_sdk import (
            Client,  # Verified: Client exposes list_models(); Cursor does not.
        )

        models = Client().list_models()
    else:
        models = list_models()
    divergences = catalog_divergences(project_live_catalog(models))
    if divergences:
        raise CapabilityDescriptorDrift("; ".join(divergences))


def resolve_cursor(model: str | ModelId) -> CursorSdkModelConfig:
    """Resolve a bare or ``cursor/``-prefixed model id to a trusted config."""
    bare = canonical_cursor_bare_id(str(model))
    if is_cursor_model_denied(bare):
        raise ValueError(f"cursor model {bare!r} is denied")
    cfg = _TRUSTED_CURSOR_MODELS.get(bare)
    if cfg is not None:
        return cfg
    return CursorSdkModelConfig(model_id=bare, params=())


def validate_knobs(config: CursorSdkModelConfig, overrides: Mapping[str, str]) -> None:
    """Validate knob overrides; raises ``ValueError`` with all errors collected."""
    errors: list[str] = []
    known = {spec.name: spec for spec in config.params}
    for name, value in overrides.items():
        spec = known.get(name)
        if spec is None:
            errors.append(f"unknown knob {name!r} for model {config.model_id!r}")
            continue
        if value not in spec.accepted:
            errors.append(f"knob {name!r} value {value!r} not in {list(spec.accepted)}")
    if errors:
        raise ValueError("; ".join(errors))


def build_model_selection(
    config: CursorSdkModelConfig,
    overrides: Mapping[str, str] | None = None,
) -> ModelSelection:
    """Build ``ModelSelection`` with default-omit knob emission."""
    knob_overrides = dict(overrides or {})
    validate_knobs(config, knob_overrides)
    params: list[ModelParameterValue] = []
    for spec in config.params:
        if spec.name in knob_overrides:
            params.append(
                ModelParameterValue(id=spec.name, value=knob_overrides[spec.name])
            )
        elif spec.default is not None:
            params.append(ModelParameterValue(id=spec.name, value=spec.default))
    return ModelSelection(id=config.model_id, params=tuple(params))
