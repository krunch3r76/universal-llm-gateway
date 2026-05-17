"""Phase 1.0a selection strategies (7 working + set_aggregation stub).

Implements the strategies listed in work-order sidecar. `set_aggregation` raises
per Phase 1.0a scope (traversal is 1.0b; synthesis 1.5).

Adapted from prior reference (largely correct); message adjusted to exact
1.0a expectation for test_selection.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from .errors import SelectionError

STRATEGIES: set[str] = {
    "all",
    "newest_n_by_observed_at",
    "highest_confidence_n",
    "predicate_filter",
    "derivation_filter",
    "temporal_window",
    "composite",
    "set_aggregation",
}


def _coerce_dt(val: str | datetime | None) -> datetime:
    if val is None:
        return datetime.min
    if isinstance(val, datetime):
        return val
    try:
        return datetime.fromisoformat(val.replace("Z", "+00:00"))
    except Exception:
        return datetime.min


def _observed_key(item: dict[str, Any]) -> datetime:
    return _coerce_dt(
        item.get("observed_at") or item.get("created_at") or item.get("valid_from")
    )


def _confidence_key(item: dict[str, Any]) -> float:
    # Mirrors scoring.py / AssertionItem ordering
    score = item.get("confidence_score")
    if score is not None:
        return float(score)
    conf = (item.get("confidence") or "hypothesized").lower()
    return {"confirmed": 1.0, "believed": 0.75, "suspected": 0.5, "hypothesized": 0.25}.get(
        conf, 0.0
    )


def select(
    items: list[dict[str, Any]], strategy: str, **params: Any
) -> list[dict[str, Any]]:
    """Apply named strategy to list of decoded assertion rows.

    All strategies implicitly respect superseded_by IS NULL (caller pre-filters).
    Returns new list (does not mutate input). Enriched with selection metadata.
    """
    if strategy not in STRATEGIES:
        raise SelectionError(f"unknown strategy: {strategy}")

    if not items:
        return []

    if strategy == "all":
        out = list(items)
        for it in out:
            it.setdefault("_selection", {})["mode"] = "all"
        return out

    if strategy == "newest_n_by_observed_at":
        n = int(params.get("n", 5))
        out = sorted(items, key=_observed_key, reverse=True)[:n]
        for it in out:
            it.setdefault("_selection", {})["mode"] = f"newest_n:{n}"
        return out

    if strategy == "highest_confidence_n":
        n = int(params.get("n", 5))
        out = sorted(items, key=_confidence_key, reverse=True)[:n]
        for it in out:
            it.setdefault("_selection", {})["mode"] = f"highest_conf_n:{n}"
        return out

    if strategy == "predicate_filter":
        pred = params.get("predicate") or params.get("contains", "")
        if not pred:
            return list(items)
        out = [it for it in items if pred.lower() in (it.get("claim") or "").lower()]
        for it in out:
            it.setdefault("_selection", {})["mode"] = "predicate_filter"
        return out

    if strategy == "derivation_filter":
        allowed = set(params.get("allowed") or params.get("derivation_types") or [])
        if not allowed:
            return list(items)
        out = [it for it in items if (it.get("derivation_type") or "") in allowed]
        for it in out:
            it.setdefault("_selection", {})["mode"] = "derivation_filter"
        return out

    if strategy == "temporal_window":
        since = _coerce_dt(params.get("since"))
        until = _coerce_dt(params.get("until") or datetime.max)
        out = [
            it
            for it in items
            if since <= _observed_key(it) <= until
        ]
        for it in out:
            it.setdefault("_selection", {})["mode"] = "temporal_window"
        return out

    if strategy == "composite":
        chain: list[tuple[str, dict[str, Any]]] = params.get("chain") or []
        current = list(items)
        for sub, sub_params in chain:
            current = select(current, sub, **sub_params)
        for it in current:
            it.setdefault("_selection", {})["mode"] = f"composite:{len(chain)}"
        return current

    if strategy == "set_aggregation":
        set_entity_id = params.get("set_entity_id") or params.get("set_id")
        if not set_entity_id or not set_entity_id.startswith("set:"):
            raise SelectionError(f"set_aggregation requires set: entity_id, got: {set_entity_id!r}")

        # Phase 1.0b: traverse has_member edges, return member assertions.
        # Phase 1.5 will add aggregate-claim synthesis on top of this.
        from ..relationship_sql import fetch_relationships
        member_relationships = fetch_relationships(
            source_id=set_entity_id,
            type_id="has_member",
        )
        member_entity_ids = {r["target_id"] for r in member_relationships}

        # Filter items to those belonging to member entities, dedupe by id
        out: list[dict[str, Any]] = []
        seen: set[Any] = set()
        for item in items:
            if item.get("entity_id") in member_entity_ids and item.get("id") not in seen:
                out.append(item)
                seen.add(item.get("id"))

        for it in out:
            it.setdefault("_selection", {})["mode"] = f"set_aggregation:{set_entity_id}"
        return out

    return list(items)


__all__ = ["STRATEGIES", "select"]
