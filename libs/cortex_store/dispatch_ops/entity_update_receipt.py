"""Default MCP ``entity_update`` response — small receipt vs full card echo."""

from __future__ import annotations

import hashlib
import json
from typing import Any


def entity_detail_sha256(card: dict[str, Any]) -> str:
    """SHA-256 (hex) of the post-update EntityDetail read-back.

    Hashes UTF-8 bytes of ``json.dumps(card, sort_keys=True, separators=(',', ':'),
    default=str)`` where ``card`` is exactly the dict returned by
    ``get_entity_impl(conn, entity_id=…, source='boot')`` — the same projection as
    ``entity_get`` with default ``intent='full'`` (entity row, active assertions,
    relationships, and bundled read-side fields on that path).
    """
    payload = json.dumps(card, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def entity_update_card_call(entity_id: str) -> str:
    """Exact ``entity_get`` invocation for the hashed EntityDetail projection."""
    args = json.dumps({"entity_id": entity_id}, separators=(",", ":"))
    return f'cortex(tool="entity_get", arguments=\'{args}\')'


def collect_entity_update_warnings(full_result: dict[str, Any]) -> list[dict[str, Any]]:
    """Advisory payloads present on the full ``entity_update`` echo body."""
    warnings: list[dict[str, Any]] = []
    birth = full_result.get("endeavor_birth_warning")
    if isinstance(birth, dict):
        warnings.append(birth)
    for item in full_result.get("validation_warnings") or []:
        if isinstance(item, dict):
            warnings.append(item)
    return warnings


def build_entity_update_receipt(
    *,
    card: dict[str, Any],
    changed_keys: list[str],
    warnings: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Receipt body for default ``entity_update`` (no ``echo=\"card\"``)."""
    entity_id = str(card.get("id") or "")
    updated_at = card.get("updated_at")
    receipt: dict[str, Any] = {
        "id": entity_id,
        "updated_at": updated_at,
        "sha256": entity_detail_sha256(card),
        "changed_keys": sorted(changed_keys),
        "card_call": entity_update_card_call(entity_id),
    }
    if warnings:
        receipt["warnings"] = warnings
    return receipt
