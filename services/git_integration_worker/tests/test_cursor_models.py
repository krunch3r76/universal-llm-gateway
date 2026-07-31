"""Tests for ``cursor_models`` registry and knob validation."""

from __future__ import annotations

import pytest
from cursor_capabilities import CURSOR_MODEL_CAPABILITIES
from cursor_sdk.types import (
    ModelParameterDefinition,
    ModelParameterDefinitionValue,
    ModelParameterValue,
    ModelVariant,
    SDKModel,
)

from services.git_integration_worker.cursor_models import (
    CapabilityDescriptorDrift,
    assert_capability_descriptor_fresh,
    build_model_selection,
    catalog_divergences,
    project_live_catalog,
    resolve_cursor,
    validate_knobs,
)


def test_resolve_cursor_bare_hit() -> None:
    cfg = resolve_cursor("composer-2.5")
    assert cfg.model_id == "composer-2.5"
    assert len(cfg.params) == 1


def test_resolve_cursor_prefixed_hit() -> None:
    cfg = resolve_cursor("cursor/composer-2.5")
    assert cfg.model_id == "composer-2.5"


def test_resolve_cursor_miss() -> None:
    with pytest.raises(ValueError, match="not in trusted allowlist"):
        resolve_cursor("cursor/unknown-model")


def test_resolve_cursor_wrong_provider_reject() -> None:
    with pytest.raises(ValueError, match="provider 'anthropic'"):
        resolve_cursor("anthropic/claude-opus-4-8")


def test_registry_matches_descriptor() -> None:
    for model_id, capability in CURSOR_MODEL_CAPABILITIES.items():
        cfg = resolve_cursor(model_id)
        assert cfg.model_id == model_id
        assert {spec.name for spec in cfg.params} == set(capability.knobs)


def test_build_model_selection_default_omit() -> None:
    cfg = resolve_cursor("claude-opus-5")
    selection = build_model_selection(cfg)
    assert selection.id == "claude-opus-5"
    emitted = {p.id: p.value for p in selection.params}
    assert emitted == {}


def test_build_model_selection_reasoning_models_no_fast_default() -> None:
    """Reasoning cursor models must NOT default fast=true (quality degradation)."""
    for model in ("claude-opus-5", "claude-opus-4-8", "claude-sonnet-4-6"):
        selection = build_model_selection(resolve_cursor(model))
        emitted = {p.id: p.value for p in selection.params}
        assert "fast" not in emitted, f"{model} should not default fast"
    with pytest.raises(ValueError, match="unknown knob 'fast'"):
        build_model_selection(resolve_cursor("claude-sonnet-4-6"), {"fast": "true"})


def test_build_model_selection_composer_default_fast() -> None:
    cfg = resolve_cursor("composer-2.5")
    selection = build_model_selection(cfg)
    assert selection.params[0].value == "true"


def test_build_model_selection_override() -> None:
    cfg = resolve_cursor("composer-2.5")
    selection = build_model_selection(cfg, {"fast": "true"})
    assert len(selection.params) == 1
    assert selection.params[0].value == "true"


def test_build_model_selection_opus_effort_low() -> None:
    selection = build_model_selection(
        resolve_cursor("claude-opus-5"), {"effort": "low"}
    )
    assert {p.id: p.value for p in selection.params} == {"effort": "low"}


def test_validate_knobs_collect_all() -> None:
    cfg = resolve_cursor("composer-2.5")
    with pytest.raises(ValueError, match="unknown knob 'bogus'"):
        validate_knobs(cfg, {"bogus": "x", "fast": "maybe"})
    with pytest.raises(ValueError, match="not in"):
        validate_knobs(cfg, {"fast": "maybe"})


def _stub_sdk_model(model_id: str) -> SDKModel:
    capability = CURSOR_MODEL_CAPABILITIES[model_id]
    parameters = [
        ModelParameterDefinition(
            id=name,
            display_name=name,
            values=[
                ModelParameterDefinitionValue(value=value, display_name=value)
                for value in spec.accepted
            ],
        )
        for name, spec in capability.knobs.items()
    ]
    default_params = [
        ModelParameterValue(id=name, value=value)
        for name, value in capability.default_variant.items()
    ]
    return SDKModel(
        id=model_id,
        display_name=model_id,
        description=model_id,
        parameters=parameters,
        variants=[
            ModelVariant(
                params=default_params,
                display_name="default",
                description="default",
                is_default=True,
            )
        ],
    )


def test_assert_capability_descriptor_fresh_stubbed_catalog() -> None:
    models = [_stub_sdk_model(model_id) for model_id in CURSOR_MODEL_CAPABILITIES]
    assert_capability_descriptor_fresh(list_models=lambda: models)


def test_assert_capability_descriptor_fresh_raises_on_divergence() -> None:
    diverged = SDKModel(
        id="composer-2.5",
        display_name="composer-2.5",
        description="composer-2.5",
        parameters=[
            ModelParameterDefinition(
                id="fast",
                display_name="fast",
                values=[
                    ModelParameterDefinitionValue(value="false", display_name="false"),
                    ModelParameterDefinitionValue(value="true", display_name="true"),
                    ModelParameterDefinitionValue(value="turbo", display_name="turbo"),
                ],
            )
        ],
        variants=[
            ModelVariant(
                params=[ModelParameterValue(id="fast", value="true")],
                display_name="default",
                description="default",
                is_default=True,
            )
        ],
    )
    projected = project_live_catalog([diverged])
    assert catalog_divergences(projected)
    with pytest.raises(CapabilityDescriptorDrift):
        assert_capability_descriptor_fresh(list_models=lambda: [diverged])


@pytest.mark.skipif(
    not __import__("os").environ.get("CURSOR_SDK_LIVE_CATALOG_CHECK"),
    reason="set CURSOR_SDK_LIVE_CATALOG_CHECK=1 for live cursor catalog drift check",
)
def test_assert_capability_descriptor_fresh_live_catalog() -> None:
    assert_capability_descriptor_fresh()
