"""Lightweight resource state representation for monitoring."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class ResourceState:
    """Current state of a resource."""

    resource_id: str
    status: str
    memory_mb: int
    last_updated: datetime
    process_pid: int | None = None
    metrics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable representation."""
        data = {
            "resource_id": self.resource_id,
            "status": self.status,
            "memory_mb": self.memory_mb,
            "last_updated": self.last_updated.isoformat(),
            "metrics": self.metrics,
        }
        if self.process_pid is not None:
            data["process_pid"] = self.process_pid
        return data
