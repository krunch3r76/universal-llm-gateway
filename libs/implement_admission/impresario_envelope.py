"""Impresario envelope checks split from impresario_policy.py."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from universal_protocol.errors import ProtocolError

from implement_admission.impresario_policy import (
    ALLOWED_SURFACES,
    SeatSpec,
    _validate_effort_for_model,
    policy_snapshot_sha256,
)


def render_knobs(seat: SeatSpec) -> dict[str, Any]:
    """Translate canonical rung to wire knobs for *seat*."""
    model = seat.model
    rung = seat.effort_rung
    _validate_effort_for_model(model, rung)
    if model.startswith("cdp/"):
        return {"reasoning_effort": rung}
    wire = rung
    if rung == "extra":
        wire = "xhigh"
    knobs: dict[str, str] = {"effort": wire}
    if model.startswith("cursor/grok"):
        knobs["fast"] = "false"
    return {"model_knobs": knobs}


def validate_impresario_envelope(
    envelope: Mapping[str, Any] | None,
    *,
    expected_sha: str | None = None,
) -> dict[str, Any]:
    """Validate commission envelope; raise ``ProtocolError`` when incomplete."""
    if not isinstance(envelope, dict):
        raise ProtocolError(
            code="IMPRESARIO_ENVELOPE_MISSING",
            message="conductor admit requires generation_options.impresario envelope",
            source="rpc",
            retryable=False,
        )
    house_root = envelope.get("house_root")
    surface = envelope.get("surface")
    if not house_root or not surface:
        raise ProtocolError(
            code="IMPRESARIO_ENVELOPE_MISSING",
            message="impresario envelope requires house_root and surface",
            source="rpc",
            retryable=False,
        )
    if surface not in ALLOWED_SURFACES:
        raise ProtocolError(
            code="IMPRESARIO_ENVELOPE_MISSING",
            message=f"surface {surface!r} not in {sorted(ALLOWED_SURFACES)!r}",
            source="rpc",
            retryable=False,
        )
    sha = envelope.get("policy_sha256")
    live_sha = expected_sha or policy_snapshot_sha256()
    if not sha or str(sha) != live_sha:
        raise ProtocolError(
            code="POLICY_SNAPSHOT_MISMATCH",
            message="impresario policy_sha256 does not match live snapshot",
            source="rpc",
            retryable=False,
            data={"expected": live_sha, "got": sha},
        )
    return dict(envelope)


def validate_conductor_generation_options(
    generation_options: Mapping[str, Any] | None,
) -> None:
    """Gate conductor admits when ``generation_options`` is present."""
    if generation_options is None:
        return
    if not isinstance(generation_options, dict):
        raise ProtocolError(
            code="IMPRESARIO_ENVELOPE_MISSING",
            message="generation_options must be a mapping",
            source="rpc",
            retryable=False,
        )
    imp = generation_options.get("impresario")
    if imp is None:
        raise ProtocolError(
            code="IMPRESARIO_ENVELOPE_MISSING",
            message="generation_options present but impresario envelope missing",
            source="rpc",
            retryable=False,
        )
    validate_impresario_envelope(imp if isinstance(imp, dict) else None)
