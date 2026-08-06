"""Matter-scoped contact role salience and exclusion-assertion detection.

Audit 2026-08-05 §D1/D2: person entities carry explicit role_in_matter;
exclusion assertions (\"X is NOT the origin\") rank higher on entity cards.
"""

from __future__ import annotations

import json
import re
from typing import Any

ROLE_IN_MATTER_VALUES = frozenset(
    {"documented", "witnessed", "incidental", "excluded"}
)

_EXCLUSION_CLAIM_RE = re.compile(
    r"(?i)"
    r"(?:did\s+NOT|should\s+not\s+be\s+read|NOT\s+produce|NOT\s+the\s+origin"
    r"|NOT\s+responsible|excluded\s+from|provenance\s+correction)"
)


def role_in_matter_from_attributes(attributes: object) -> str | None:
    """Return validated role_in_matter from entity attributes JSON/dict."""
    if not isinstance(attributes, dict):
        if isinstance(attributes, str) and attributes.strip():
            try:
                attributes = json.loads(attributes)
            except json.JSONDecodeError:
                return None
        else:
            return None
    raw = attributes.get("role_in_matter")
    if not isinstance(raw, str):
        return None
    value = raw.strip().lower()
    return value if value in ROLE_IN_MATTER_VALUES else None


def is_exclusion_assertion(
    claim: str,
    attributes: dict[str, object] | None = None,
) -> bool:
    """True when an assertion is an explicit exclusion / provenance correction."""
    if attributes and attributes.get("exclusion_assertion") is True:
        return True
    return bool(_EXCLUSION_CLAIM_RE.search(claim))


def format_person_role_summary(entity: dict[str, Any]) -> str | None:
    """One-line role salience for person cards."""
    role = role_in_matter_from_attributes(entity.get("attributes"))
    if not role:
        return None
    matter = ""
    attrs = entity.get("attributes")
    if isinstance(attrs, dict):
        matter = str(attrs.get("role_matter_id") or "")
    label = ""
    if isinstance(attrs, dict) and attrs.get("role_label"):
        label = f" — {attrs['role_label']}"
    matter_bit = f" on {matter}" if matter else ""
    return f"role_in_matter: {role}{matter_bit}{label}"


def format_matter_contact_registry(attributes: object) -> str | None:
    """Render matter_contact_roles registry from event/org attributes."""
    if not isinstance(attributes, dict):
        if isinstance(attributes, str) and attributes.strip():
            try:
                attributes = json.loads(attributes)
            except json.JSONDecodeError:
                return None
        else:
            return None
    registry = attributes.get("matter_contact_roles")
    if not isinstance(registry, list) or not registry:
        return None
    parts: list[str] = []
    for entry in registry:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name") or entry.get("person_id") or "?"
        role = entry.get("role_in_matter") or "?"
        note = entry.get("note")
        line = f"{name}={role}"
        if note:
            line += f" ({note})"
        parts.append(line)
    if not parts:
        return None
    return "matter_contacts: " + "; ".join(parts)
