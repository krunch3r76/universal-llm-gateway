"""Shared trait-axis vocabulary for cortex_store status cutover."""

from __future__ import annotations

from dataclasses import dataclass, field

CONFIDENCE_BAND_VALUES = frozenset({"unsubstantiated", "provisional", "confirmed"})

LIFECYCLE_VALUES = frozenset(
    {
        "active",
        "deprecated",
        "dismissed",
        "draft",
        "invalidated",
        "merged",
        "reaped",
        "retired",
        "superseded",
    }
)

# Non-live subset of LIFECYCLE_VALUES: a lifecycle in this set tombstones an
# entity (excluded from alias binding / bare-token resolution). NULL lifecycle
# is the live default and is NOT in this set. Canonical SOT for the cortex_store
# consumers and the predicate_form documented local mirror.
# Note: "draft" is intentionally excluded — draft entities remain live/resolvable.
NON_LIVE_LIFECYCLE = frozenset({"deprecated", "merged", "reaped", "retired"})

ADOPTION_VALUES = frozenset({"proposed", "adopted", "superseded"})

PROVISIONAL_BIRTH_TYPES = frozenset({"decision"})


@dataclass
class TraitBackfillCounts:
    """Per-trait write counts for a backfill run."""

    confidence_band: int = 0
    lifecycle: int = 0
    adoption: int = 0
    entities_touched: int = 0
    by_type: dict[str, int] = field(default_factory=dict)


@dataclass
class TraitCompletenessCounts:
    """Null counts per trait column for a post-052 completeness scan."""

    total: int = 0
    null_confidence_band: int = 0
    null_lifecycle: int = 0
    null_adoption_decisions: int = 0
    by_type: dict[str, int] = field(default_factory=dict)


def default_confidence_band_for_type(entity_type: str) -> str:
    """Default ``confidence_band`` when the column is NULL (post-052 backfill)."""
    if entity_type == "transcript":
        return "confirmed"
    if entity_type in PROVISIONAL_BIRTH_TYPES:
        return "provisional"
    return "unsubstantiated"


__all__ = [
    "ADOPTION_VALUES",
    "CONFIDENCE_BAND_VALUES",
    "LIFECYCLE_VALUES",
    "NON_LIVE_LIFECYCLE",
    "PROVISIONAL_BIRTH_TYPES",
    "TraitBackfillCounts",
    "TraitCompletenessCounts",
    "default_confidence_band_for_type",
]
