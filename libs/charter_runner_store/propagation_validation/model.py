"""Propagation validation row model and shared helpers."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from ..propagation_code_ref_mint import require_resolvable_code_ref

_TERMINAL_OUTCOMES = frozenset(
    {"validated", "unvalidated_timeout", "contradicted", "superseded"}
)
_SUPERSEDED_PREFIX = "superseded_by:"


@dataclass(frozen=True)
class PropagationValidation:
    """One restart-bound activation observation, not a liveness oracle."""

    validation_id: str
    row_id: str | None
    service: str
    code_ref: str
    restart_intent: str | None
    restart_boundary_monotonic: float | None
    pre_observation: dict[str, Any] | None
    post_observation: dict[str, Any] | None
    observed_code_version: str | None
    code_ref_relation: str | None
    identity_measurement: str | None
    outcome: str
    failure_reason: str | None
    kill_boundary_at: str | None
    boundary_source: str | None
    created_at: float
    updated_at: float


def store_code_ref(code_ref: str, *, service: str) -> str:
    return require_resolvable_code_ref(code_ref, service=service)


def from_row(row) -> PropagationValidation:
    def parse(value):
        return json.loads(value) if value else None

    keys = row.keys()
    return PropagationValidation(
        validation_id=str(row["validation_id"]),
        row_id=row["row_id"],
        service=str(row["service"]),
        code_ref=str(row["code_ref"]),
        restart_intent=row["restart_intent"],
        restart_boundary_monotonic=row["restart_boundary_monotonic"],
        pre_observation=parse(row["pre_observation"]),
        post_observation=parse(row["post_observation"]),
        observed_code_version=row["observed_code_version"],
        code_ref_relation=row["code_ref_relation"],
        identity_measurement=row["identity_measurement"],
        outcome=str(row["outcome"]),
        failure_reason=row["failure_reason"],
        kill_boundary_at=row["kill_boundary_at"] if "kill_boundary_at" in keys else None,
        boundary_source=row["boundary_source"] if "boundary_source" in keys else None,
        created_at=float(row["created_at"]),
        updated_at=float(row["updated_at"]),
    )


def as_dict(record: PropagationValidation | None) -> dict[str, Any] | None:
    if record is None:
        return None
    return {
        key: value
        for key, value in record.__dict__.items()
        if key not in {"pre_observation", "post_observation"}
    } | {
        "pre_observation": record.pre_observation,
        "post_observation": record.post_observation,
    }


__all__ = [
    "PropagationValidation",
    "_SUPERSEDED_PREFIX",
    "_TERMINAL_OUTCOMES",
    "as_dict",
    "from_row",
    "store_code_ref",
]
