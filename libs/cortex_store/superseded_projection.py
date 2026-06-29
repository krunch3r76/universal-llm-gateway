"""Superseded-row breadcrumb + bounded-correction projection for entity_get."""

from __future__ import annotations

import json
from collections import Counter
from typing import Any

from .db import decode_row
from .models import SupersededBreadcrumb, SupersededCorrection

_ASSERTION_JSON_FIELDS = frozenset({"evidence_uris", "attributes"})
_TRUNC = 120
_DEEPEN_TEMPLATE = "entity_get(intent=full-historical) | assertion_get(id)"


def _decode_attributes(raw: object) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _resolve_revision_type(successor_row: dict[str, Any] | None) -> str:
    if successor_row is None:
        return "legacy_unclassified"
    attrs = _decode_attributes(successor_row.get("attributes"))
    rtype = attrs.get("revision_type")
    return rtype if isinstance(rtype, str) and rtype else "legacy_unclassified"


def _material_delta(
    predecessor: dict[str, Any], successor: dict[str, Any] | None
) -> bool:
    if successor is None:
        return False
    p = decode_row(predecessor, _ASSERTION_JSON_FIELDS)
    s = decode_row(successor, _ASSERTION_JSON_FIELDS)
    p_claim = (p.get("claim") or "").strip()
    s_claim = (s.get("claim") or "").strip()
    if p_claim != s_claim:
        return True
    if p.get("confidence") != s.get("confidence"):
        return True
    p_uris = p.get("evidence_uris") or []
    s_uris = s.get("evidence_uris") or []
    if len(p_uris) != len(s_uris):
        return True
    return p.get("derivation_type") != s.get("derivation_type")


def _emit_correction(
    predecessor: dict[str, Any],
    *,
    revision_type: str,
    successor: dict[str, Any] | None,
) -> SupersededCorrection:
    p_claim = (predecessor.get("claim") or "")[:_TRUNC]
    s_claim = (successor.get("claim") or "")[:_TRUNC] if successor else None
    pred_id = int(predecessor["id"])
    return SupersededCorrection(
        id=pred_id,
        revision_type=revision_type,
        prior_claim_trunc=p_claim,
        new_claim_trunc=s_claim,
        superseded_by=int(predecessor["superseded_by"]),
        deepen=f"assertion_get({pred_id})",
    )


def build_superseded_projection(
    assertion_rows: list[dict[str, Any]],
) -> tuple[SupersededBreadcrumb, list[SupersededCorrection]]:
    superseded_rows = [r for r in assertion_rows if r.get("superseded_by") is not None]
    by_id = {int(r["id"]): r for r in assertion_rows}
    type_counts: Counter[str] = Counter()
    corrections: list[SupersededCorrection] = []

    for predecessor in superseded_rows:
        successor_id = predecessor.get("superseded_by")
        successor = by_id.get(int(successor_id)) if successor_id is not None else None
        revision_type = _resolve_revision_type(successor)
        type_counts[revision_type] += 1
        if revision_type == "correction" or (
            revision_type == "legacy_unclassified"
            and _material_delta(predecessor, successor)
        ):
            corrections.append(
                _emit_correction(
                    predecessor,
                    revision_type=revision_type,
                    successor=successor,
                )
            )

    breadcrumb = SupersededBreadcrumb(
        count=len(superseded_rows),
        ids=[int(r["id"]) for r in superseded_rows],
        by_revision_type=dict(type_counts),
        deepen_template=_DEEPEN_TEMPLATE,
    )
    return breadcrumb, corrections
