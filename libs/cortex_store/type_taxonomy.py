"""Genus vocabulary SOT for Cortex entity types.

Matter genus (design-locked matter-playbook-lifecycle §4.0):
  - species ∈ ``MATTER_SPECIES`` are standing matter handles
  - ``mode`` ∈ ``MATTER_MODES`` is an optional per-type attribute
  - genus is immutable at birth; duplicate handles use ``entity_merge``;
    retype away from a matter species is blocked unless ``force=True``

``involves`` contract (v1 documented, not enforced):
  A matter handle (``type ∈ MATTER_SPECIES``) ``involves`` 0..n targets;
  handle→``case:`` is the canonical matter↔formal-record link; ``role`` names
  the target's function. Broader live uses remain valid.

Pattern authority: work-item-genus-registry D1 (code frozenset + category filter).
"""

from __future__ import annotations

MATTER_SPECIES = frozenset({"work", "finance", "case", "opportunity"})
MATTER_MODES = frozenset({"conflict", "stewardship", "endeavor"})

CATEGORY_SPECIES: dict[str, frozenset[str]] = {
    "matter": MATTER_SPECIES,
}


def category_species(category: str) -> frozenset[str] | None:
    """Return the species frozenset for *category*, or None when unknown."""
    return CATEGORY_SPECIES.get(category)
