"""
Provenance tracking for pipeline artifacts.

Invariants:
- originator_model_id set exactly once at content creation
- lineage tracks ALL processors (models AND utilities)
- Independence checks compare originator_model_id ONLY
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True, kw_only=True, frozen=True)
class Provenance:
    """
    Immutable provenance record for content artifacts.

    Attributes:
        originator_model_id: Model that authored the original content
        originator_step_id: Pipeline step that created it
        lineage: Processing chain as ("step_id:processor", ...)
    """

    originator_model_id: str
    originator_step_id: str
    lineage: tuple[str, ...] = ()

    def with_processor(
        self,
        step_id: str,
        processor_model_id: str | None = None,
    ) -> Provenance:
        """
        Extend lineage with a processor step.

        Args:
            step_id: The step that processed this artifact
            processor_model_id: Model ID if model-based, None for system/utility steps

        Returns:
            New Provenance with extended lineage, same originator
        """
        processor_label = processor_model_id or "system"
        return Provenance(
            originator_model_id=self.originator_model_id,
            originator_step_id=self.originator_step_id,
            lineage=self.lineage + (f"{step_id}:{processor_label}",),
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict for JSON embedding."""
        return {
            "originator_model_id": self.originator_model_id,
            "originator_step_id": self.originator_step_id,
            "lineage": list(self.lineage),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Provenance:
        """Deserialize from dict."""
        return cls(
            originator_model_id=data["originator_model_id"],
            originator_step_id=data["originator_step_id"],
            lineage=tuple(data.get("lineage", [])),
        )


def create_provenance(model_id: str, step_id: str) -> Provenance:
    """
    Create new provenance for content origination.

    Use when a model generates new content (not transforming existing).

    Args:
        model_id: The model that authored this content
        step_id: The pipeline step that created it

    Returns:
        Fresh Provenance with empty lineage
    """
    return Provenance(
        originator_model_id=model_id,
        originator_step_id=step_id,
    )
