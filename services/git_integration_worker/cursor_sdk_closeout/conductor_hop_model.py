"""Successor model/knobs for conductor hop reactor re-admit (todo:continuity-hop-cost-fixes)."""

from __future__ import annotations

from typing import Any

from bus_watch.spawn_wake.packet import conductor_hop_recipe_model_fields


def _normalize_knobs(raw: Any) -> dict[str, str] | None:
    if not isinstance(raw, dict):
        return None
    out = {str(k): str(v) for k, v in raw.items()}
    return out or None


def _todo_attrs_for_work_key(work_key: str) -> dict[str, Any]:
    slug_ref = work_key.strip()
    if not slug_ref.startswith("todo:"):
        return {}
    try:
        from implement_admission.closeout_runtime import get_runtime

        resp = get_runtime().dispatch(
            "entity_get",
            {"entity_id": slug_ref, "intent": "card"},
        )
    except Exception:  # noqa: BLE001 — recipe default when cortex is unavailable
        return {}
    if not isinstance(resp, dict):
        return {}
    attrs = resp.get("attributes")
    return attrs if isinstance(attrs, dict) else {}


def resolve_hop_successor_model_fields(
    row: dict[str, Any],
    rec: dict[str, Any],
) -> dict[str, Any]:
    """Model and knobs for the next hop ``team_dispatch`` body.

    Precedence: ``hop_reactor_model`` / ``hop_reactor_model_knobs`` on the
    predecessor ledger record, then todo attrs ``conductor_hop_model`` /
    ``conductor_hop_model_knobs``, then the K97 conductor-admit recipe.
    """
    recipe = conductor_hop_recipe_model_fields()

    reactor_model = str(rec.get("hop_reactor_model") or "").strip() or None
    reactor_knobs = _normalize_knobs(rec.get("hop_reactor_model_knobs"))
    if reactor_model or reactor_knobs:
        out: dict[str, Any] = {"model": reactor_model or recipe.get("model")}
        if reactor_knobs:
            out["model_knobs"] = reactor_knobs
        elif recipe.get("model_knobs"):
            out["model_knobs"] = dict(recipe["model_knobs"])
        return out

    work_key = str(
        row.get("work_key") or rec.get("source_ref") or row.get("source_ref") or ""
    ).strip()
    attrs = _todo_attrs_for_work_key(work_key)
    todo_model = str(attrs.get("conductor_hop_model") or "").strip() or None
    todo_knobs = _normalize_knobs(attrs.get("conductor_hop_model_knobs"))
    if todo_model or todo_knobs:
        out = {"model": todo_model or recipe.get("model")}
        if todo_knobs:
            out["model_knobs"] = todo_knobs
        elif recipe.get("model_knobs"):
            out["model_knobs"] = dict(recipe["model_knobs"])
        return out

    return dict(recipe)


__all__ = ["resolve_hop_successor_model_fields"]
