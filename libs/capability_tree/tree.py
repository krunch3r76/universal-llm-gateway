"""Build and query the capability tree. Pipeline source wins id collisions."""

from __future__ import annotations

import difflib
from dataclasses import dataclass, field

from .members import CapabilityMember, CapabilitySource

_NEAR_MATCH_LIMIT = 5
_NEAR_MATCH_CUTOFF = 0.6


@dataclass
class CapabilityTree:
    """In-memory projection. Rebuilt per request; not stored on the registry."""

    members: dict[str, CapabilityMember] = field(default_factory=dict)
    skips: list[dict[str, str]] = field(default_factory=list)

    @classmethod
    def build(
        cls,
        pipeline: CapabilitySource,
        satellites: tuple[CapabilitySource, ...] = (),
    ) -> CapabilityTree:
        tree = cls()
        for source in (pipeline, *satellites):
            for member in source.members():
                if member.id in tree.members:
                    tree.skips.append(
                        {
                            "pipeline_id": member.id,
                            "reason": "member_id_collision",
                            "category": member.category,
                        }
                    )
                    continue
                tree.members[member.id] = member
        return tree

    def resolve(self, member_id: str) -> CapabilityMember | None:
        return self.members.get(member_id)

    def by_category(self, name: str) -> dict[str, CapabilityMember]:
        return {
            member_id: member
            for member_id, member in self.members.items()
            if member.category == name
        }

    def near_matches(self, member_id: str) -> list[str]:
        return difflib.get_close_matches(
            member_id,
            list(self.members),
            n=_NEAR_MATCH_LIMIT,
            cutoff=_NEAR_MATCH_CUTOFF,
        )
