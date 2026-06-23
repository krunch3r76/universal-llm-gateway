"""Versioned, surface-agnostic `executor_recommendation` object builder.

Assembles the additive advisory object emitted on dispatch responses (op=handoff
now; API-role generate later). Shares POLICY FACTS with other surfaces via
``recommend_knobs`` / ``validate_knobs``; each surface owns its own trigger and
wiring. Fork-E (assertion 20513): model, thinking, and effort are INDEPENDENT
advisory axes -- never collapse effort into thinking.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Final

from cursor_capabilities import DESCRIPTOR_VERSION

from .dispatch_knob_policy import recommend_knobs, validate_knobs

SCHEMA_VERSION: Final[str] = "1"
_POLICY_ID: Final[str] = "executor-knobs-v1"
_POLICY_VERSION: Final[str] = "2026-06-23"


def _descriptor_id() -> str:
    return f"libs/cursor_capabilities@{DESCRIPTOR_VERSION}"


def _validate_against_target(
    *, target_model: str, knobs: Mapping[str, str]
) -> dict[str, Any]:
    """Validate recommended knobs against the TARGET model descriptor.

    Clamp-not-silent: unsupported/invalid knobs null the resolved axis and emit a
    warning while the caller keeps the policy-intent values. ``status`` is
    ``"valid"`` when every axis is accepted, else ``"partial"``.
    """
    per_knob = validate_knobs(model_id=target_model, knobs=knobs)
    warnings: list[str] = []
    normalized: dict[str, str | None] = dict(knobs)
    status = "valid"
    for name, verdict in per_knob.items():
        if verdict == "unsupported_knob":
            warnings.append(f"{name}_unsupported_on_{target_model}")
            normalized[name] = None
            status = "partial"
        elif verdict == "invalid_value":
            warnings.append(f"{name}_invalid_value_for_{target_model}")
            normalized[name] = None
            status = "partial"
    return {
        "status": status,
        "descriptor": _descriptor_id(),
        "per_knob": per_knob,
        "normalized_knobs": normalized,
        "warnings": warnings,
    }


def build_executor_recommendation(
    *,
    contract: str,
    target_surface: str,
    target_model: str,
    task_nature: str | None = None,
    classification_source: str = "packet_contract",
) -> dict[str, Any]:
    """Build the additive, versioned ``executor_recommendation`` object.

    Always returns a fully-formed CONTAINER (never ``None`` / empty). Knob values
    are ``None`` when ``status != "recommended"``. ``target_model`` is the model the
    recommended ``{effort, thinking}`` axes are validated against -- NOT
    ``knobs.model`` (which names an alternative low-cost executor). ``model``,
    ``thinking`` and ``effort`` are emitted as independent keys (Fork-E): a value on
    one axis never implies a value on another.
    """
    rec = recommend_knobs(contract=contract, task_nature=task_nature)

    policy_block = {
        "id": _POLICY_ID,
        "version": _POLICY_VERSION,
        "rationale_code": rec.rationale_code,
        "rationale": rec.rationale,
    }
    classification = {
        "contract": contract,
        "task_nature": task_nature,
        "confidence": "high" if rec.status == "recommended" else "low",
        "source": classification_source,
    }

    if rec.status != "recommended":
        return {
            "schema_version": SCHEMA_VERSION,
            "advisory": True,
            "status": "none",
            "target_surface": target_surface,
            "classification": classification,
            "policy": policy_block,
            "knobs": {"model": None, "thinking": None, "effort": None},
            "validation": {
                "status": "skipped",
                "descriptor": _descriptor_id(),
                "normalized_knobs": {
                    "model": None,
                    "thinking": None,
                    "effort": None,
                },
                "warnings": ["no_recommendation_to_validate"],
            },
            "override_allowed": True,
        }

    # Independent-axis validation against the target seat model (Fork-E preserved:
    # effort and thinking are validated and emitted separately).
    knob_axes = {"effort": rec.effort, "thinking": rec.thinking}
    validation = _validate_against_target(target_model=target_model, knobs=knob_axes)
    status = "recommended" if validation["status"] == "valid" else "partial"
    return {
        "schema_version": SCHEMA_VERSION,
        "advisory": True,
        "status": status,
        "target_surface": target_surface,
        "classification": classification,
        "policy": policy_block,
        "knobs": {"model": rec.model, "thinking": rec.thinking, "effort": rec.effort},
        "validation": validation,
        "override_allowed": True,
    }
