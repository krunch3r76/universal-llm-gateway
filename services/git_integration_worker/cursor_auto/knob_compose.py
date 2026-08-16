"""Card-gated knob merge for cursor-auto nested cursor-sdk dispatches.

Wire resolution (``desired_model`` / ``desired_effort`` / contract) lives in
``wire_map``. This module is the second hop: map a resolved effort onto the
model card, and fill Grok ``fast=false`` when the caller omitted ``fast``.
Both ``clamp_effort_to_model_card`` and ``compose_model_knobs`` share
``resolve_card_effort`` so off-ladder tokens cannot fail open on one surface
and drop the knob on the other.
"""

from __future__ import annotations

from typing import Any

from cursor_capabilities import (
    canonical_cursor_bare_id,
    effort_knob_name,
    supported_knobs,
)
from effort_vocabulary import WIRE_LADDER

from services.git_integration_worker.cursor_auto.wire_map import _MODEL_TABLE


def clamp_effort_to_accepted(
    requested: str, accepted: tuple[str, ...]
) -> str | None:
    """Pick the highest accepted rung at or below *requested* on the effort ladder.

    Descriptors disagree on ceiling (grok tops at ``xhigh``, opus at ``max``), so a
    verbatim mismatch must degrade rather than drop the knob — dropping silently
    hands the bridge a model default that can be far above what was asked for.
    """
    if requested in accepted:
        return requested
    if requested not in WIRE_LADDER:
        return None
    in_ladder = [value for value in WIRE_LADDER if value in accepted]
    if not in_ladder:
        return None
    below = [
        value
        for value in in_ladder
        if WIRE_LADDER.index(value) <= WIRE_LADDER.index(requested)
    ]
    return below[-1] if below else in_ladder[0]


def capability_bare_id(model_id: str) -> str:
    """Resolve a wire alias or ``cursor/`` id onto a capability-card key.

    Auto bindable aliases (``sonnet-5``, ``opus-5``) live in ``_MODEL_TABLE``;
    descriptor keys are catalog bare ids (``claude-sonnet-5``, ``claude-opus-5``).
    ``canonical_cursor_bare_id`` alone leaves ``sonnet-5`` as ``sonnet-5``,
    which is not a card key — effort then fails open.
    """
    raw = str(model_id or "").strip()
    if not raw:
        raise ValueError("empty model id")
    prefixed = _MODEL_TABLE.get(raw.lower())
    if prefixed is not None:
        return canonical_cursor_bare_id(prefixed)
    return canonical_cursor_bare_id(raw)


def resolve_card_effort(
    model_id: str, requested: str
) -> tuple[str | None, str | None]:
    """Map *requested* onto the model's effort-like knob.

    Returns ``(value, clamp_note)``. ``value`` is None when the model has no
    effort-like knob or the id does not resolve to a card. ``clamp_note`` is
    None on identity (already on-card). Off-ladder values (``none``,
    ``minimal``) fall to the card ``KnobSpec.default`` so
    ``clamp_effort_to_model_card`` and ``compose_model_knobs`` agree — the
    former used to leave ``resolved_effort`` unchanged while the latter
    dropped the knob.
    """
    try:
        bare = capability_bare_id(model_id)
    except ValueError:
        return None, None
    name = effort_knob_name(bare)
    if name is None:
        return None, None
    spec = supported_knobs(bare).get(name)
    if spec is None:
        return None, None
    accepted = tuple(spec.accepted)
    if requested in accepted:
        return requested, None
    mapped = clamp_effort_to_accepted(requested, accepted)
    if mapped is not None:
        note = (
            f"{requested}→{mapped} (not on {bare} card; "
            f"accepted {name}={','.join(accepted)})"
        )
        return mapped, note
    fallback = spec.default if spec.default in accepted else None
    if fallback is None and accepted:
        fallback = accepted[0]
    if fallback is None:
        return None, None
    note = (
        f"{requested}→{fallback} (off-ladder; {bare} card default "
        f"{name}={fallback})"
    )
    return fallback, note


def compose_model_knobs(
    model: dict[str, Any],
    effort: dict[str, Any],
) -> dict[str, str]:
    """Merge the resolved effort onto a model's knobs, capability-clamped.

    ``resolve_desired_model`` only carries model-intrinsic knobs (opus thinking);
    without this merge the resolved effort is reported on the admit turn but never
    reaches the bridge, so every Auto-bound reasoner ran at its catalog default.

    Grok's ListModels catalog default is ``fast=true`` (speed over quality). Auto
    fills ``fast=false`` when the knob is absent so the bridge never inherits
    that catalog speed default. This is a **default, not a pin** — an explicit
    ``fast`` already on ``model_knobs`` is preserved. Catalog
    ``default_variant`` stays ListModels-true for freshness parity. No standing
    Auto intent uses grok-4.6 ``fast=true``.
    """
    knobs: dict[str, str] = dict(model.get("model_knobs") or {})
    model_id = str(model.get("resolved_model_id") or "").strip()
    bare: str | None = None
    if model_id:
        try:
            bare = capability_bare_id(model_id)
        except ValueError:
            bare = None
        else:
            if (
                bare == "grok-4.6"
                and "fast" in supported_knobs(bare)
                and "fast" not in knobs
            ):
                knobs["fast"] = "false"
    requested = str(effort.get("resolved_effort") or "").strip().lower()
    if not model_id or not requested:
        return knobs
    value, _note = resolve_card_effort(model_id, requested)
    if value is None:
        return knobs
    name = effort_knob_name(bare) if bare is not None else None
    if name is None:
        return knobs
    knobs[name] = value
    return knobs
