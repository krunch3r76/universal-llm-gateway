"""
Base pipeline event contract.

All pipeline events inherit from PipelineEvent. The EventRecorder
auto-populates identity fields (pipeline_id, execution_id, timestamps,
sequence) so callers only provide step_name and event-specific data.

Invariant: ∀ event ∈ events.jsonl: event.sequence is monotonically increasing
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


def _camel_to_snake(name: str) -> str:
    """Convert CamelCase to snake_case for event_type derivation."""
    s = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", name)
    return re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s).lower()


@dataclass(slots=True, kw_only=True)
class PipelineEvent:
    """
    Base contract for all pipeline events.

    Recorder-managed fields (auto-populated by EventRecorder.emit):
        pipeline_id, execution_id, event_type, timestamp_ms, wall_clock, sequence

    Caller-provided fields:
        step_name (required for step-level events, "" for pipeline-level)
        model_id (optional, set when a specific model is involved)
    """

    # Identity (auto-populated by recorder)
    pipeline_id: str = ""
    execution_id: str = ""
    event_type: str = ""

    # Ordering (auto-populated by recorder)
    timestamp_ms: float = 0.0
    wall_clock: str = ""
    sequence: int = 0

    # Caller-provided
    step_name: str = ""
    model_id: str | None = None

    def __post_init__(self) -> None:
        """Auto-derive event_type from class name if not set."""
        if not self.event_type:
            self.event_type = _camel_to_snake(type(self).__name__)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict for JSONL output (traverses full slot hierarchy)."""
        result: dict[str, Any] = {}
        for cls in type(self).__mro__:
            for f in getattr(cls, "__slots__", ()):
                val = getattr(self, f)
                if val is not None:
                    result[f] = val
        return result
