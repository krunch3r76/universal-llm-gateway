"""Capability members and the source protocol."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class CapabilityMember:
    """One invocable capability projected from a source."""

    id: str
    category: str
    source: str
    summary: dict[str, object]

    @property
    def canonical_url(self) -> str:
        return f"/api/v1/capabilities/{self.category}/{self.id}"


class CapabilitySource(Protocol):
    """A producer of capability members. Pipeline registry is the only live source."""

    def name(self) -> str: ...

    def members(self) -> list[CapabilityMember]: ...
