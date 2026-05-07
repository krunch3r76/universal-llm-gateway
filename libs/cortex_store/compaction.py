"""Compaction-pointer read semantics (Cortex v2.4 §6.10).

Assertion claims fall into three categories when an entity has been compacted:

- **Compaction pointer** — claim matches ``Compacted into archive summary <id>``
  (supersede-output row written during a pointer-compaction pass)
- **Consolidation summary** — claim starts with ``archive summary``
  (the single summary assertion written before superseding the pointers)
- **Other** — all remaining live operative assertions

Default read behaviour (§6.10):
  1. Consolidation summary surfaces first.
  2. Compaction pointers deprioritised to the end (not omitted, so audit
     consumers can still see them without flag-flipping).
  3. Tombstone-only entities (all active assertions are compaction pointers)
     collapse to the summary alone plus a navigation hint derived from
     ``archives_to`` relationship edges.

Callers performing structural audit pass ``include_compaction_pointers=True``
to receive the unfiltered assertion stream.

Detection uses claim-text prefix matching — a structural field (e.g.
``derivation_type`` tag) would be more robust and should be preferred once
the write path is amended to set one.
"""

from __future__ import annotations

import re
from typing import Any

# §6.10 detection patterns
_POINTER_RE = re.compile(r"^Compacted into archive summary \d+", re.IGNORECASE)
_POINTER_ID_RE = re.compile(r"Compacted into archive summary (\d+)", re.IGNORECASE)
_SUMMARY_RE = re.compile(r"^archive summary", re.IGNORECASE)

# Aggregate-surface filter (todo:cortex-aggregate-compaction-filter):
# SQLite LIKE patterns used by read surfaces to discriminate pointer / summary
# rows at the SQL level rather than post-filter row-by-row.
POINTER_SQL_LIKE = "Compacted into archive summary %"
SUMMARY_SQL_LIKE = "archive summary%"


def is_compaction_pointer(claim: str) -> bool:
    """True if *claim* is a compaction-pointer assertion."""
    return bool(_POINTER_RE.match(claim))


def is_tombstone_only(claims: list[str]) -> bool:
    """True if every claim in *claims* is a compaction pointer.

    ∀ c ∈ claims: is_compaction_pointer(c). Empty list returns False — a
    zero-assertion entity is not tombstoned, it is empty.
    """
    if not claims:
        return False
    return all(is_compaction_pointer(c) for c in claims)


def synthesize_predicate_summary(
    et_type_counts: list[dict[str, Any]],
    archives_to_children: list[str],
) -> str:
    """Edge-derived heuristic predicate summary (v2.4 §6.3 / §6.7 fallback).

    Deterministic. No LLM dependency. Produces a non-None summary from
    relationship-type aggregates and archives_to children already materialized
    by the card fetch plan — fulfills the §6.3 contract that the
    ``predicate_summary`` slot is never None when the heuristic path runs.

    Returns empty string when the entity has no active relationships and no
    archival children.

    ∀ r ∈ et_type_counts: r has keys ``type_id: str`` and ``count: int``.
    """
    parts: list[str] = []
    for r in et_type_counts:
        parts.append(f"{r['type_id']}({r['count']})")
    if archives_to_children:
        children_str = ", ".join(archives_to_children)
        parts.append(f"archived_into([{children_str}])")
    return "; ".join(parts)


def is_consolidation_summary(claim: str) -> bool:
    """True if *claim* is a consolidation-summary assertion."""
    return bool(_SUMMARY_RE.match(claim))


def extract_summary_ids(assertions: list[dict[str, Any]]) -> list[int]:
    """Return the distinct summary assertion IDs referenced by compaction pointers.

    These IDs can be used for a supplementary point-lookup when the summary
    assertion is outside the current query window (e.g. cut off by LIMIT because
    it was created before the pointers that reference it).
    """
    ids: set[int] = set()
    for a in assertions:
        m = _POINTER_ID_RE.search(a.get("claim") or "")
        if m:
            ids.add(int(m.group(1)))
    return list(ids)


def filter_compaction_pointers(
    assertions: list[dict[str, Any]],
    *,
    include_compaction_pointers: bool = False,
) -> tuple[list[dict[str, Any]], int]:
    """Strict-exclude variant for aggregate (cross-entity) read surfaces.

    Unlike :func:`apply_compaction_filter` (which deprioritizes pointers within
    a single entity's stream per §6.10), this helper *removes* compaction-pointer
    assertions entirely. Used by aggregate surfaces — FTS search, list-style
    enumeration with no ``entity_id`` filter, review_queue — where pointer rows
    are pure bookkeeping noise.

    Returns ``(filtered_assertions, pointer_count)``.
    """
    if include_compaction_pointers or not assertions:
        return assertions, 0
    kept: list[dict[str, Any]] = []
    pointer_count = 0
    for a in assertions:
        if is_compaction_pointer(a.get("claim") or ""):
            pointer_count += 1
        else:
            kept.append(a)
    return kept, pointer_count


def apply_compaction_filter(
    assertions: list[dict[str, Any]],
    *,
    include_compaction_pointers: bool = False,
    archives_to_children: list[str] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """Apply §6.10 compaction-aware projection to *assertions*.

    Returns ``(reordered_assertions, projection_meta | None)``.

    ``projection_meta`` is ``None`` when no compaction pattern was detected
    or when *include_compaction_pointers* is ``True`` (raw-stream mode).

    *archives_to_children* — list of entity IDs reached via ``archives_to``
    relationship edges from the owning entity.  Used to build the navigation
    hint in the tombstone-collapsed case.  Gracefully degrades when absent.
    """
    if include_compaction_pointers or not assertions:
        return assertions, None

    pointers: list[dict[str, Any]] = []
    summaries: list[dict[str, Any]] = []
    others: list[dict[str, Any]] = []

    for a in assertions:
        claim: str = a.get("claim") or ""
        if is_compaction_pointer(claim):
            pointers.append(a)
        elif is_consolidation_summary(claim):
            summaries.append(a)
        else:
            others.append(a)

    if not pointers and not summaries:
        # No compaction pattern — leave the stream unchanged.
        return assertions, None

    # Tombstone-only: every *active* assertion is a compaction pointer.
    # ∀ a ∈ active: is_compaction_pointer(a.claim)
    active = [a for a in assertions if a.get("superseded_by") is None]
    if active and all(is_compaction_pointer(a.get("claim") or "") for a in active):
        children = archives_to_children or []
        hint = f"archived → see children [{', '.join(children)}]"
        return summaries, {
            "mode": "tombstone_collapsed",
            "pointer_count": len(pointers),
            "children": children,
            "navigation_hint": hint,
        }

    # Mixed: summaries → others → pointers (deprioritised, not omitted).
    return summaries + others + pointers, {
        "mode": "pointers_deprioritized",
        "pointer_count": len(pointers),
        "summary_count": len(summaries),
    }
