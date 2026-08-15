"""Closed vocabulary for lane parentage roles (I5 — no ``root`` token)."""

from __future__ import annotations

LANE_ROLES: frozenset[str] = frozenset(
    {
        "sub_mission",
        "operator_proxy",
        "hop",
        "spillover",
        "dispatch",
        "side",
        "parallel",
    }
)


def parse_lane_role(value: str) -> str:
    """Return canonical lane role or raise ``ValueError`` when unknown."""
    role = value.strip().lower()
    if role not in LANE_ROLES:
        raise ValueError(
            f"lane_role must be one of {sorted(LANE_ROLES)!r}, got {value!r}"
        )
    return role


__all__ = ["LANE_ROLES", "parse_lane_role"]
