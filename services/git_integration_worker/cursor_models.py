"""Executor-local cursor model registry and knob validation for cursor-sdk dispatches."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from cursor_sdk.types import ModelParameterValue, ModelSelection
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


_TRUSTED_CURSOR_MODELS: dict[str, CursorSdkModelConfig] = {
    "composer-2.5": CursorSdkModelConfig(
        model_id="composer-2.5",
        params=(
            CursorKnobSpec(name="fast", accepted=("true", "false"), default="true"),
        ),
    ),
    "claude-opus-4-8": CursorSdkModelConfig(
        model_id="claude-opus-4-8",
        params=(
            CursorKnobSpec(name="thinking", accepted=("true", "false")),
            CursorKnobSpec(name="context", accepted=("200k", "300k", "1m")),
            CursorKnobSpec(name="effort", accepted=("low", "medium", "high", "xhigh")),
            CursorKnobSpec(name="fast", accepted=("true", "false"), default="true"),
        ),
    ),
    "claude-sonnet-4-6": CursorSdkModelConfig(
        model_id="claude-sonnet-4-6",
        params=(
            CursorKnobSpec(name="fast", accepted=("true", "false"), default="true"),
        ),
    ),
}

def resolve_cursor(model: str | ModelId) -> CursorSdkModelConfig:
    """Resolve a bare or ``cursor/``-prefixed model id to a trusted config."""
    parsed = ModelId.parse(model)
    if parsed.provider is not None and parsed.provider != "cursor":
        raise ValueError(
            f"model {parsed.original!r} has provider {parsed.provider!r}; "
            f"cursor executor accepts bare ids or 'cursor/' prefix only"
        )
    bare = parsed.api_model_id
    cfg = _TRUSTED_CURSOR_MODELS.get(bare)
    if cfg is None:
        raise ValueError(
            f"cursor model {bare!r} not in trusted allowlist; "
            f"valid: {sorted(_TRUSTED_CURSOR_MODELS)}"
        )
    return cfg


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
