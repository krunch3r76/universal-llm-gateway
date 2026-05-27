"""Structured target-model resolution result.

A :class:`ResolvedTargetModel` is the frozen output of step-level model
resolution: the chosen ``model_id`` string plus its parsed :class:`ModelId`
form and the provenance label naming which resolution branch produced it.
Coordination, fallback eligibility, and the generate handler all consume this
dataclass instead of re-deriving the routing identity from raw step fields,
which keeps gating / eviction protection / queueing aligned with what the
handler actually executes against.

The dataclass is intentionally frozen + slotted: it travels through async
boundaries and is compared by value, so mutation would break correlator
joins that key off ``(execution_id, model_id)``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from model_id import ModelId


@dataclass(frozen=True, slots=True)
class ResolvedTargetModel:
    """Structured model resolution result for coordination and fallback."""

    model_id: str
    parsed_model_id: ModelId
    resolution_source: Literal[
        "pipeline_runtime_override",
        "model_ref_override",
        "model_requirements",
        "registry_model_ref",
        "raw_model_ref",
    ]
    model_ref: str | None = None
    requirements_source: str | None = None

    @classmethod
    def build(
        cls,
        model_id: str,
        *,
        resolution_source: Literal[
            "pipeline_runtime_override",
            "model_ref_override",
            "model_requirements",
            "registry_model_ref",
            "raw_model_ref",
        ],
        model_ref: str | None = None,
        requirements_source: str | None = None,
    ) -> ResolvedTargetModel:
        return cls(
            model_id=model_id,
            parsed_model_id=ModelId.parse(model_id),
            resolution_source=resolution_source,
            model_ref=model_ref,
            requirements_source=requirements_source,
        )

    @property
    def is_local(self) -> bool:
        return not self.parsed_model_id.is_cloud

    @property
    def is_cloud(self) -> bool:
        return self.parsed_model_id.is_cloud

    @property
    def came_from_registry_model_ref(self) -> bool:
        return self.resolution_source == "registry_model_ref"

    @property
    def came_from_model_requirements(self) -> bool:
        return self.resolution_source == "model_requirements"
