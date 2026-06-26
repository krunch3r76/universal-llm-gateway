"""Condition card adapter — Card v0 projection for condition entities.

Drives the condition card with lifecycle-centric status semantics and
redaction-aware field projection via ``condition_redaction``.

Section manifest mirrors the standard five-section pattern with an added
``triggers`` section for the child-todo summary (distinct from reasoning_edges).
"""

from __future__ import annotations

import json
from typing import Any

from .base import BaseCardAdapter, CardAdapterCounts
from ..condition_redaction import CONFLICT, redact
from ..models import CardSection


class ConditionAdapter(BaseCardAdapter):
    type_name = "condition"

    expected_section_ids = (
        "assertions",
        "assertions_superseded",
        "relationships",
        "triggers",
        "reasoning_edges",
    )

    label_assertions = "Evidence (active)"
    label_assertions_superseded = "Evidence (superseded)"
    label_relationships = "Related entities"
    label_triggers = "Triggered child todos"
    label_reasoning_edges = "Reasoning edges"

    def sections(
        self, entity: dict[str, Any], counts: CardAdapterCounts
    ) -> list[CardSection]:
        return [
            CardSection(id="assertions", label=self.label_assertions, count=counts["active_n"]),
            CardSection(
                id="assertions_superseded",
                label=self.label_assertions_superseded,
                count=counts["superseded_n"],
            ),
            CardSection(id="relationships", label=self.label_relationships, count=counts["rel_total"]),
            CardSection(
                id="triggers",
                label=self.label_triggers,
                count=counts.get("archives_to_count", 0),  # type: ignore[call-overload]
            ),
            CardSection(id="reasoning_edges", label=self.label_reasoning_edges, count=counts["edges_n"]),
        ]

    def status_summary(self, entity: dict[str, Any]) -> dict[str, Any] | None:
        attrs = _decode_attributes(entity.get("attributes"))
        lifecycle = entity.get("workflow_state") or attrs.get("lifecycle")
        safety_invariant = bool(attrs.get("safety_invariant", False))
        reveal_default = str(attrs.get("reveal_default", "open"))

        summary: dict[str, Any] = {
            "lifecycle": lifecycle,
            "safety_invariant": safety_invariant,
            "reveal_default": reveal_default,
            "updated_at": entity.get("updated_at"),
        }

        # Surface triggers summary (child todo count hint) when present
        triggers_summary = attrs.get("triggers_summary")
        if triggers_summary:
            summary["triggers_summary"] = triggers_summary

        # Surface recategorization history count (not body) for boot context
        recat_history = attrs.get("recategorization_history")
        if isinstance(recat_history, list) and recat_history:
            summary["recategorization_count"] = len(recat_history)

        return summary

    def summary_row(self, entity: dict[str, Any]) -> str | None:
        attrs = _decode_attributes(entity.get("attributes"))
        narrative = str(attrs.get("narrative", ""))
        if narrative:
            return narrative.split("\n")[0][:200]
        return str(entity.get("description") or "")


def condition_card_with_redaction(
    entity: dict[str, Any],
    *,
    surface: str,
    audience: str,
) -> dict[str, Any]:
    """Return the card-level projection for *entity* at the given surface/audience.

    Applies condition_redaction to determine which fields to include.
    When CONFLICT is returned, the card body is replaced with an escalation
    marker — callers MUST NOT surface this card to the audience.
    """
    attrs = _decode_attributes(entity.get("attributes"))
    reveal_default = str(attrs.get("reveal_default", "open"))
    sv_raw = attrs.get("surface_visibility")
    surface_visibility = sv_raw if isinstance(sv_raw, dict) else None
    safety_invariant = bool(attrs.get("safety_invariant", False))

    # Normalise audience to AudienceClass
    aud: Any = audience if audience in ("orchestrator_lead", "sub_agent", "log_sink") else "sub_agent"

    level = redact(
        reveal_default=reveal_default,
        surface_visibility=surface_visibility,
        safety_invariant=safety_invariant,
        surface=surface,
        audience=aud,
    )

    if level == CONFLICT:
        return {
            "entity_id": entity.get("id"),
            "type": "condition",
            "redaction_level": CONFLICT,
            "action_required": "ESCALATE_TO_ORCHESTRATOR",
        }

    if level == "hidden":
        return {}

    narrative = str(attrs.get("narrative", ""))
    if level == "sanitized":
        return {
            "entity_id": entity.get("id"),
            "type": "condition",
            "lifecycle": entity.get("workflow_state"),
            "reveal_default": reveal_default,
            "safety_invariant": safety_invariant,
            "narrative_head": narrative.split("\n")[0][:200] if narrative else "",
            "redaction_level": "sanitized",
        }

    return {
        "entity_id": entity.get("id"),
        "type": "condition",
        "lifecycle": entity.get("workflow_state"),
        "attributes": attrs,
        "redaction_level": "full",
    }


def _decode_attributes(raw: object) -> dict[str, Any]:
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            decoded = json.loads(raw)
            return decoded if isinstance(decoded, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}
