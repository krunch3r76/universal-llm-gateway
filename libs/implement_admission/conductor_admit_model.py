"""Conductor admit model resolution — pipeline defaults + explicit caller pins."""

from __future__ import annotations

from typing import Any

from bus_watch.spawn_wake.packet import play_conductor_model_fields
from cursor_capabilities import canonical_cursor_bare_id

from implement_admission.conductor_policy_snapshot import (
    ConductorKnobSnapshot,
    model_for_seat_role,
)
from implement_admission.team_dispatch_admit_config import load_conductor_model_defaults

_COMPOSER_BARE = "composer-2.5"
_DRIVER_FALLBACK = "cursor/composer-2.5"


def _normalize_knobs(raw: Any) -> dict[str, str] | None:
    if not isinstance(raw, dict):
        return None
    out = {str(k): str(v) for k, v in raw.items()}
    return out or None


def _model_knobs_from_opts(opts: dict[str, Any]) -> dict[str, str] | None:
    raw = opts.get("model_knobs")
    if isinstance(raw, dict):
        return _normalize_knobs(raw)
    gen = opts.get("generation_options") or {}
    if isinstance(gen, dict):
        return _normalize_knobs(gen.get("model_knobs"))
    return None


def _seat_block_model(block: Any) -> str | None:
    if not isinstance(block, dict):
        return None
    model = str(block.get("model") or "").strip()
    return model or None


def _drop_composer_effort(model: str, knobs: dict[str, str] | None) -> dict[str, str] | None:
    bare = canonical_cursor_bare_id(model.rsplit("/", 1)[-1])
    if bare != _COMPOSER_BARE or not knobs:
        return knobs
    return {k: v for k, v in knobs.items() if k != "effort"} or None


def _warn_allowed_models(model: str, allowed: list[Any] | None) -> list[str]:
    if not allowed:
        return []
    allowed_set = {str(m).strip() for m in allowed if str(m).strip()}
    if not allowed_set or model in allowed_set:
        return []
    return [
        f"model {model!r} outside pipeline model_defaults.conductor.allowed_models "
        f"{sorted(allowed_set)!r}; admitted with warning"
    ]


def _todo_attrs_from_opts(opts: dict[str, Any]) -> dict[str, Any]:
    test_attrs = opts.get("_test_todo_attrs")
    if isinstance(test_attrs, dict):
        return test_attrs
    source_ref = str(opts.get("source_ref") or "").strip()
    if not source_ref.startswith("todo:"):
        return {}
    try:
        from implement_admission.closeout_runtime import get_runtime

        resp = get_runtime().dispatch(
            "entity_get",
            {"entity_id": source_ref, "intent": "card"},
        )
    except Exception:  # noqa: BLE001 — fall through when cortex unavailable
        return {}
    if not isinstance(resp, dict):
        return {}
    attrs = resp.get("attributes")
    return attrs if isinstance(attrs, dict) else {}


def resolve_conductor_admit_model(
    opts: dict[str, Any],
    *,
    snapshot: ConductorKnobSnapshot,
    seat_role: str,
) -> tuple[str, dict[str, str] | None, list[str]]:
    """Return ``(model, model_knobs, warnings)`` for conductor defaults step."""
    warnings: list[str] = []

    explicit = str(opts.get("model") or "").strip()
    if explicit:
        knobs = _drop_composer_effort(explicit, _model_knobs_from_opts(opts))
        return explicit, knobs, warnings

    attrs = _todo_attrs_from_opts(opts)
    todo_model = str(attrs.get("conductor_hop_model") or "").strip() or None
    todo_knobs = _normalize_knobs(attrs.get("conductor_hop_model_knobs"))
    if todo_model or todo_knobs:
        model = todo_model or _DRIVER_FALLBACK
        knobs = _drop_composer_effort(
            model, todo_knobs or _model_knobs_from_opts(opts)
        )
        return model, knobs, warnings

    conductor_opts = opts.get("conductor")
    if not isinstance(conductor_opts, dict):
        gen = opts.get("generation_options") or {}
        if isinstance(gen, dict):
            block = gen.get("conductor")
            conductor_opts = block if isinstance(block, dict) else {}
        else:
            conductor_opts = {}
    pipeline_opts = opts.get("pipeline_options") or {}
    if isinstance(pipeline_opts, dict) and "conductor" in pipeline_opts:
        block = pipeline_opts.get("conductor")
        if isinstance(block, dict):
            conductor_opts = {**conductor_opts, **block}

    if conductor_opts.get("rebind_policy"):
        rebind_model = _seat_block_model(conductor_opts.get("driver"))
        if rebind_model:
            knobs = _drop_composer_effort(rebind_model, _normalize_knobs(conductor_opts.get("driver")))
            return rebind_model, knobs, warnings

    play_policy = opts.get("play_policy")
    if isinstance(play_policy, dict):
        play_fields = play_conductor_model_fields(play_policy)
        play_model = str(play_fields.get("model") or "").strip()
        if play_model:
            knobs = _drop_composer_effort(
                play_model, _normalize_knobs(play_fields.get("model_knobs"))
            )
            return play_model, knobs, warnings

    defaults = load_conductor_model_defaults()
    if defaults:
        role_block = defaults.get(seat_role) or defaults.get("driver")
        block_model = _seat_block_model(role_block)
        if block_model:
            raw_knobs: dict[str, str] | None = None
            if isinstance(role_block, dict):
                raw_knobs = {
                    str(k): str(v)
                    for k, v in role_block.items()
                if str(k) not in {"model", "effort"}
            } or None
            if raw_knobs and "fast" in raw_knobs:
                raw_knobs["fast"] = raw_knobs["fast"].lower()
            knobs = _drop_composer_effort(block_model, raw_knobs)
            warnings.extend(
                _warn_allowed_models(block_model, defaults.get("allowed_models"))
            )
            return block_model, knobs, warnings

    floor = model_for_seat_role(snapshot, seat_role)
    knobs = _drop_composer_effort(floor, None)
    if not floor:
        floor = _DRIVER_FALLBACK
        knobs = {"fast": "false"}
    return floor, knobs, warnings


def resolve_hop_successor_default_from_pipeline() -> dict[str, Any]:
    """Resolved hop_successor block for birth stamping (may be empty)."""
    defaults = load_conductor_model_defaults()
    block = defaults.get("hop_successor")
    if not isinstance(block, dict):
        return {}
    model = str(block.get("model") or _DRIVER_FALLBACK).strip()
    out: dict[str, Any] = {"model": model}
    knobs = _normalize_knobs({k: v for k, v in block.items() if k != "model"})
    if knobs:
        out["model_knobs"] = _drop_composer_effort(model, knobs) or knobs
    elif canonical_cursor_bare_id(model.rsplit("/", 1)[-1]) == _COMPOSER_BARE:
        out["model_knobs"] = {"fast": "false"}
    return out
