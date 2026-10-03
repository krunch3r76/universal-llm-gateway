"""Capability tree collision and near-match cap."""

from __future__ import annotations

import pytest

from capability_tree.members import CapabilityMember
from capability_tree.tree import CapabilityTree

pytestmark = pytest.mark.offline


class _Source:
    def __init__(self, name: str, members: list[CapabilityMember]) -> None:
        self._name = name
        self._members = members

    def name(self) -> str:
        return self._name

    def members(self) -> list[CapabilityMember]:
        return list(self._members)


def _member(member_id: str) -> CapabilityMember:
    return CapabilityMember(
        id=member_id,
        category="demo",
        source="pipeline",
        summary={"url": f"/api/v1/capabilities/demo/{member_id}"},
    )


def test_satellite_collision_keeps_pipeline_and_records_skip() -> None:
    pipeline = _Source("pipeline", [_member("shared")])
    satellite = _Source("satellite", [_member("shared")])
    tree = CapabilityTree.build(pipeline, satellites=(satellite,))
    assert tree.resolve("shared") is not None
    assert tree.resolve("shared").source == "pipeline"
    assert tree.skips == [
        {"pipeline_id": "shared", "reason": "member_id_collision", "category": "demo"}
    ]


def test_near_matches_cap_is_five() -> None:
    ids = [f"alpha-{index}" for index in range(12)]
    tree = CapabilityTree.build(_Source("pipeline", [_member(item) for item in ids]))
    matches = tree.near_matches("alpha-0")
    assert len(matches) <= 5
    assert "alpha-0" not in matches or len(matches) <= 5
