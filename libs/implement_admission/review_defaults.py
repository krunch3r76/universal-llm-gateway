"""Per-artifact-kind review defaults from ``route_policy.yaml`` ``review_defaults``."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from model_id import ModelId

from implement_admission.routing import load_route_policy

_ALLOWED_ROW_KEYS = frozenset({"model", "contract", "effort"})


@dataclass(frozen=True, slots=True)
class ReviewDefault:
    model: str
    contract: str | None
    effort: str | None


def load_review_default(
    kind: str,
    *,
    policy: Mapping[str, Any] | None = None,
) -> ReviewDefault:
    """Return the configured review default for *kind*."""
    if policy is None:
        policy = load_route_policy()
    review_defaults = policy.get("review_defaults")
    if not isinstance(review_defaults, dict):
        raise ValueError("route policy missing review_defaults mapping")
    row = review_defaults.get(kind)
    if not isinstance(row, dict):
        raise ValueError(f"review_defaults missing entry for kind {kind!r}")
    for key in row:
        if key not in _ALLOWED_ROW_KEYS:
            raise ValueError(
                f"review_defaults.{kind} has unknown key {key!r}; "
                f"allowed: {sorted(_ALLOWED_ROW_KEYS)}"
            )
    model = row.get("model")
    if not isinstance(model, str) or not model.strip():
        raise ValueError(f"review_defaults.{kind}.model must be a non-empty string")
    model = model.strip()
    try:
        parsed = ModelId.parse(model)
    except ValueError as exc:
        raise ValueError(f"review_defaults.{kind}.model invalid: {model!r}") from exc
    if parsed.backend_type == "cloud_api":
        raise ValueError(
            f"review_defaults.{kind}.model must not be a cloud_api model: {model!r}"
        )
    contract = row.get("contract")
    if contract is not None and not isinstance(contract, str):
        raise ValueError(f"review_defaults.{kind}.contract must be a string or absent")
    effort = row.get("effort")
    if effort is not None and not isinstance(effort, str):
        raise ValueError(f"review_defaults.{kind}.effort must be a string or absent")
    return ReviewDefault(
        model=model,
        contract=contract.strip() if isinstance(contract, str) else None,
        effort=effort.strip() if isinstance(effort, str) else None,
    )
