"""Tests for conductor admit model precedence (pipeline model_defaults)."""

from __future__ import annotations

import pytest

from implement_admission.conductor_admit_model import resolve_conductor_admit_model
from implement_admission.conductor_policy_snapshot import default_knobs_from_route_policy

from pipelines.team_dispatch_admit.v1.handlers.conductor_defaults import (
    evaluate_conductor_defaults,
)
from pipelines.team_dispatch_admit.v1.handlers.validate import evaluate_validate


@pytest.mark.offline
def test_conductor_omit_model_uses_pipeline_defaults() -> None:
    snap = default_knobs_from_route_policy()
    model, knobs, warnings = resolve_conductor_admit_model(
        {"job": "conductor", "seat": "cursor-sdk"},
        snapshot=snap,
        seat_role="driver",
    )
    assert model == "cursor/composer-2.5"
    assert warnings == []
    assert knobs == {"fast": "false"}


@pytest.mark.offline
def test_explicit_model_beats_pipeline_defaults() -> None:
    snap = default_knobs_from_route_policy()
    model, knobs, _warnings = resolve_conductor_admit_model(
        {
            "job": "conductor",
            "seat": "cursor-sdk",
            "model": "cursor/composer-2.5",
            "model_knobs": {"fast": "false"},
        },
        snapshot=snap,
        seat_role="driver",
    )
    assert model == "cursor/composer-2.5"
    assert knobs == {"fast": "false"}


@pytest.mark.offline
def test_conductor_defaults_step_explicit_model_wins() -> None:
    """C7: explicit model= must appear on AdmitPlan, not route-policy driver."""
    opts = {
        "op": "generate",
        "job": "conductor",
        "seat": "cursor-sdk",
        "model": "cursor/composer-2.5",
        "hop_seq": 0,
        "source_ref": "todo:c7-explicit",
        "_skip_packet_render": True,
    }
    validate_out = evaluate_validate(opts)
    plan = evaluate_conductor_defaults(opts, validate_out)
    assert plan.get("model") == "cursor/composer-2.5"


@pytest.mark.offline
def test_allowed_models_outside_list_warns_not_rejects() -> None:
    snap = default_knobs_from_route_policy()
    _model, _knobs, warnings = resolve_conductor_admit_model(
        {
            "job": "conductor",
            "model": "cursor/grok-4.7",
        },
        snapshot=snap,
        seat_role="driver",
    )
    assert not warnings  # explicit pin skips allowlist


@pytest.mark.offline
def test_pipeline_block_warns_on_outside_allowed_models(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "implement_admission.conductor_admit_model.load_conductor_model_defaults",
        lambda: {
            "driver": {"model": "cursor/grok-4.7", "effort": "high"},
            "allowed_models": ["cursor/composer-2.5"],
        },
    )
    snap = default_knobs_from_route_policy()
    model, _knobs, warnings = resolve_conductor_admit_model(
        {"job": "conductor"},
        snapshot=snap,
        seat_role="driver",
    )
    assert model == "cursor/grok-4.7"
    assert warnings and "allowed_models" in warnings[0]
