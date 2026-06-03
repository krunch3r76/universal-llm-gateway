"""Todo adapter — workflow_state-centric status semantics."""

from __future__ import annotations

import json
from typing import Any

from .base import BaseCardAdapter


class TodoAdapter(BaseCardAdapter):
    type_name = "todo"

    expected_section_ids = (
        "assertions",
        "assertions_superseded",
        "relationships",
        "archives_to",
        "reasoning_edges",
    )

    label_assertions = "Audit trail (active)"
    label_assertions_superseded = "Audit trail (superseded)"
    label_relationships = "Linked work"
    label_archives_to = "Archived into"
    label_reasoning_edges = "Reasoning edges"

    def status_summary(self, entity: dict[str, Any]) -> dict[str, Any] | None:
        attrs = _decode_attributes(entity.get("attributes"))
        summary: dict[str, Any] = {
            "workflow_state": entity.get("workflow_state"),
            "priority": attrs.get("priority"),
            "domain": attrs.get("domain"),
            "updated_at": entity.get("updated_at"),
        }
        # Surface the closure sidecar index once the todo is closed so the card
        # points the reader at the human-readable closure summary.
        if entity.get("workflow_state") == "done" and attrs.get("closure_summary_uri"):
            summary["closure_summary_uri"] = attrs["closure_summary_uri"]
        return summary


def _decode_attributes(raw: object) -> dict[str, Any]:
    """Decode the attributes JSON column to a dict (returns empty dict on failure)."""
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
