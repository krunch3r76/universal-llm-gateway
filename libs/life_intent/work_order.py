"""Deterministic verb→lane lookup and plain-language work-order rendering."""

from __future__ import annotations

from typing import Any

from .registry import LifeIntentRegistry, VerbSpec, load_registry

_RETURN_PATH = "You'll get a reply on this conversation thread."


def lookup_lane(verb: str, registry: LifeIntentRegistry | None = None) -> str:
    reg = registry or load_registry()
    spec = reg.verbs.get(verb)
    if spec is None:
        raise KeyError(f"unknown verb: {verb}")
    return spec.lane


def lookup_verb_spec(verb: str, registry: LifeIntentRegistry | None = None) -> VerbSpec:
    reg = registry or load_registry()
    spec = reg.verbs.get(verb)
    if spec is None:
        raise KeyError(f"unknown verb: {verb}")
    return spec


def render_work_order(
    normalized_intent: dict[str, Any],
    registry: LifeIntentRegistry | None = None,
) -> str:
    """Plain-language confirmation surface — no dispatch vocabulary."""
    verb = normalized_intent["verb"]
    subject = normalized_intent["subject"]

    if verb == "investigate":
        action = (
            f"A code-seat scout will investigate \"{subject}\" and report findings back here."
        )
        cost = "Cost: one scout run."
    else:
        action = (
            f"A work item will be opened for \"{subject}\" and worked through the standard pipeline."
        )
        cost = "Cost: one work item and one scout run."

    urgency = normalized_intent.get("urgency") or "normal"
    urgency_note = ""
    if urgency == "soon":
        urgency_note = " Marked as time-sensitive."

    return f"{action} {_RETURN_PATH}{urgency_note} {cost}"


def priority_for_intent(
    normalized_intent: dict[str, Any],
    registry: LifeIntentRegistry | None = None,
) -> str:
    reg = registry or load_registry()
    spec = reg.verbs[normalized_intent["verb"]]
    urgency = normalized_intent.get("urgency") or "normal"
    return spec.priority_from_urgency.get(str(urgency), "medium")
