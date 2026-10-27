"""Default MCP ``entity_update`` response — small receipt vs full card echo."""

from __future__ import annotations

import hashlib
import json
from typing import Any


def entity_detail_sha256(card: dict[str, Any]) -> str:
    """SHA-256 of the stored entity card using stable JSON (sorted keys, compact).

    Matches the ``entity_get`` / ``intent=full`` EntityDetail projection passed in.
    """
    payload = json.dumps(card, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def entity_update_card_call(entity_id: str) -> str:
    """Exact ``entity_get`` invocation that returns the full card."""
    args = json.dumps({"entity_id": entity_id}, separators=(",", ":"))
    return f'cortex(tool="entity_get", arguments=\'{args}\')'


def build_entity_update_receipt(
    *,
    card: dict[str, Any],
    changed_keys: list[str],
) -> dict[str, Any]:
    """Receipt body for default ``entity_update`` (no ``echo=\"card\"``)."""
    entity_id = str(card.get("id") or "")
    updated_at = card.get("updated_at")
    return {
        "id": entity_id,
        "updated_at": updated_at,
        "sha256": entity_detail_sha256(card),
        "changed_keys": sorted(changed_keys),
        "card_call": entity_update_card_call(entity_id),
    }
