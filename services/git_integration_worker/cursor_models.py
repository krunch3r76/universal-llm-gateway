"""Executor-local cursor model registry and knob validation for cursor-sdk dispatches."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Final

from cursor_capabilities import (
    CURSOR_MODEL_CAPABILITIES,
    canonical_cursor_bare_id,
    catalog_divergences,
    is_cursor_model_denied,
    live_model_id,
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


def _card_knob_catalog_projection(
    models: Sequence[SDKModel],
) -> dict[str, dict[str, object]]:
    """Project live models with wire ids mapped back to card knob names."""
    raw = project_live_catalog(models)
    projected: dict[str, dict[str, object]] = {}
    for model_id, entry in raw.items():
        live_knobs = entry.get("knobs")
        card_knobs: dict[str, tuple[str, ...]] = {}
        if isinstance(live_knobs, Mapping):
            for wire_id, values in live_knobs.items():
                if not isinstance(values, Sequence) or isinstance(values, str):
                    continue
                knob = wire_id_to_card_knob(model_id, str(wire_id))
                card_knobs[knob] = tuple(str(v) for v in values)
        live_default = entry.get("default_variant")
        card_default: dict[str, str] = {}
        if isinstance(live_default, Mapping):
            for wire_id, value in live_default.items():
                card_default[wire_id_to_card_knob(model_id, str(wire_id))] = str(value)
        projected[model_id] = {
            "knobs": card_knobs,
            "default_variant": card_default,
        }
    return projected


def live_admission_error(bare_id: str, models: Sequence[SDKModel]) -> str | None:
    """Probe-side divergence for one card id; ``None`` when live matches the card."""
    capability = CURSOR_MODEL_CAPABILITIES.get(bare_id)
    if capability is None:
        return f"model {bare_id!r} not in CURSOR_MODEL_CAPABILITIES"
    projected = _card_knob_catalog_projection(models)
    live = projected.get(bare_id)
    if live is None:
        live = projected.get(live_model_id(bare_id))
    if live is None:
        return f"missing model {bare_id!r} in live catalog"
    errors: list[str] = []
    live_knobs = live.get("knobs")
    if not isinstance(live_knobs, Mapping):
        errors.append(f"model {bare_id!r}: live knobs not a mapping")
        return "; ".join(errors)
    for knob_name, spec in capability.knobs.items():
        live_values = live_knobs.get(knob_name)
        if live_values is None:
            errors.append(f"model {bare_id!r}: missing knob {knob_name!r}")
            continue
        if frozenset(live_values) != frozenset(spec.accepted):
            errors.append(
                f"model {bare_id!r}: knob {knob_name!r} accepted "
                f"{tuple(live_values)!r} != descriptor {spec.accepted!r}"
            )
    live_default = live.get("default_variant")
    if not isinstance(live_default, Mapping):
        errors.append(f"model {bare_id!r}: live default_variant not a mapping")
    elif dict(live_default) != dict(capability.default_variant):
        errors.append(
            f"model {bare_id!r}: default_variant "
            f"{dict(live_default)!r} != descriptor "
            f"{dict(capability.default_variant)!r}"
        )
    return "; ".join(errors) if errors else None


def list_live_sdk_models() -> Sequence[SDKModel]:
    """List models via ``Cursor().models.list()`` (catalog route entry point)."""
    from cursor_sdk import Cursor

    return Cursor().models.list()


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


# Live ListModels names this knob reasoning_effort. Sending effort makes the
# SDK status ERROR: Invalid parameters for registry model "grok-4.7".
_CARD_KNOB_WIRE_ID: Final[dict[tuple[str, str], str]] = {
    ("grok-4.7", "effort"): "reasoning_effort",
}


def card_knob_wire_id(model_id: str, knob_name: str) -> str:
    """Map a card knob name to the wire parameter id (identity when unmapped)."""
    return _CARD_KNOB_WIRE_ID.get((model_id, knob_name), knob_name)


def wire_id_to_card_knob(model_id: str, wire_id: str) -> str:
    """Inverse of ``card_knob_wire_id`` for one model."""
    for (mid, knob), mapped in _CARD_KNOB_WIRE_ID.items():
        if mid == model_id and mapped == wire_id:
            return knob
    return wire_id


def selected_context_window_tokens(
    model: str, emitted: Mapping[str, str]
) -> int | None:
    """Budget window for the context knob actually sent.

    ``context_window_tokens`` is the card default. A pinned ``context`` label
    replaces it so a 500k dispatch is not stopped at 256k.
    """
    from cursor_capabilities import context_window_tokens, supported_knobs

    window = context_window_tokens(model)
    label = str(emitted.get("context") or "")
    if not label:
        return window
    try:
        bare = canonical_cursor_bare_id(model)
    except ValueError:
        return window
    spec = supported_knobs(bare).get("context")
    if spec is None or label not in spec.accepted:
        return window
    if label.endswith("k") and label[:-1].isdigit():
        return int(label[:-1]) * 1_000
    return window


def build_model_selection(
    config: CursorSdkModelConfig,
    overrides: Mapping[str, str] | None = None,
) -> ModelSelection:
    """Build ``ModelSelection`` with default-omit knob emission."""
    knob_overrides = dict(overrides or {})
    validate_knobs(config, knob_overrides)
    params: list[ModelParameterValue] = []
    for spec in config.params:
        wire_id = card_knob_wire_id(config.model_id, spec.name)
        if spec.name in knob_overrides:
            params.append(
                ModelParameterValue(id=wire_id, value=knob_overrides[spec.name])
            )
        elif spec.default is not None:
            params.append(ModelParameterValue(id=wire_id, value=spec.default))
    return ModelSelection(id=live_model_id(config.model_id), params=tuple(params))
