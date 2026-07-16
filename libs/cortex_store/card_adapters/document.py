"""Document adapter — ingestion-status-centric semantics."""

from __future__ import annotations

import json
from typing import Any

from .base import BaseCardAdapter

_BINARY_SOURCE_SUFFIXES = (".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".webp")


def _decode_attributes(raw: object) -> dict[str, Any]:
    """Decode the attributes JSON column to a dict (empty on failure)."""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def ocr_companion_next_hint(status_summary: dict[str, Any] | None) -> str | None:
    """Tool-response teach for document cards (convention discovery order step 1).

    When a readable companion is registered, steer to ``fs`` that URI.
    When the binary has no companion, say so plainly and route to operator/code
    seat — no recipe, no ``extract_document`` / invent-path teach (Fable A sibling).
    Wording avoids life-intent refuse-list tokens (``op=``, ``model:``, …).
    """
    if not isinstance(status_summary, dict):
        return None
    ocr_uri = status_summary.get("ocr_uri")
    if isinstance(ocr_uri, str) and ocr_uri.strip():
        uri = ocr_uri.strip()
        return (
            f"Readable companion at status_summary.ocr_uri={uri} — "
            f'read with fs path="{uri}" (or md_read). '
            "Do not invent alternate OCR paths."
        )
    source_uri = status_summary.get("source_uri")
    if not isinstance(source_uri, str) or not source_uri.strip():
        return None
    src = source_uri.strip().lower()
    if not any(src.endswith(suf) for suf in _BINARY_SOURCE_SUFFIXES):
        return None
    return (
        "Binary source_uri present but no readable companion "
        "(attributes.ocr_uri unset). Do not invent companion paths. "
        "Ask the operator or a code seat to register one. "
        "Convention: cortex://notes/system/specs/document-ocr-companion-convention.md"
    )


class DocumentAdapter(BaseCardAdapter):
    type_name = "document"

    expected_section_ids = (
        "assertions",
        "assertions_superseded",
        "relationships",
        "archives_to",
        "reasoning_edges",
    )

    label_assertions = "Extracted claims (active)"
    label_assertions_superseded = "Extracted claims (superseded)"
    label_relationships = "Document references"
    label_archives_to = "Archived into"
    label_reasoning_edges = "Reasoning edges"

    def status_summary(self, entity: dict[str, Any]) -> dict[str, Any] | None:
        from ..status_trait_read import card_status_summary_option_c

        attrs = _decode_attributes(entity.get("attributes"))
        # Companion text for image/PDF binaries: attributes.ocr_uri (convention).
        ocr_uri = attrs.get("ocr_uri")
        extra: dict[str, Any] = {
            "source_uri": entity.get("source_uri"),
            "content_hash": entity.get("content_hash"),
            "updated_at": entity.get("updated_at"),
        }
        if isinstance(ocr_uri, str) and ocr_uri.strip():
            extra["ocr_uri"] = ocr_uri.strip()

        return card_status_summary_option_c(entity, extra=extra)
