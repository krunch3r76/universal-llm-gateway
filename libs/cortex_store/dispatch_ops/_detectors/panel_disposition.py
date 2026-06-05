"""Panel disposition completeness detectors (thread 1206, turn 14).

Session-close only — surfaces incomplete Menu D ``panel`` stamps and the
Phase-3 falsifier metric (assertion 12858).  Does not reject at assert-time.

F1 fix (1211): SOT for ``consensus_disposition`` + panel metadata is the
**assertion row** (assertions.attributes), NOT the entity attribute blob.
Per consensus-steelman-posture §3.1: audits MUST query the non-superseded
assertion; entity.attributes is a derived cache, never evidentiary.
"""

from __future__ import annotations

import json
from typing import Any

from agent_seat.panel_dispatch import (
    count_execution_evidence_uris,
    read_adjudication_artifact,
    validate_panel_assert_attributes,
)

from ...db import query
from ._shared import _finding

MIN_MATERIAL_PANEL_COHORT = 20
MIN_EXECUTION_EVIDENCE_URIS = 2


def _parse_attributes(raw: Any) -> dict[str, Any] | None:
    if not raw:
        return None
    try:
        attrs = json.loads(raw) if isinstance(raw, str) else raw
    except (json.JSONDecodeError, TypeError):
        return None
    return attrs if isinstance(attrs, dict) else None


def _is_material_panel(attrs: dict[str, Any]) -> bool:
    if attrs.get("consensus_disposition") != "panel":
        return False
    material = attrs.get("material")
    if material is False:
        return False
    if material in (0, "0", "false", "False"):
        return False
    return bool(material) if material is not None else True


def _parse_evidence_uris(raw: Any) -> list[str]:
    if not raw:
        return []
    try:
        parsed = json.loads(raw) if isinstance(raw, str) else raw
    except (json.JSONDecodeError, TypeError):
        return []
    return parsed if isinstance(parsed, list) else []


def _material_panel_rows(
    conn, subject: str | None = None
) -> list[tuple[str, dict[str, Any], list[str]]]:
    """Find material panel decisions from assertion SOT (assertion.attributes).

    ∀ result (entity_id, attributes_dict, evidence_uris): sourced from the
    latest non-superseded assertion carrying consensus_disposition=panel on a
    decision:* entity.  Returns ONLY assertions — entity.attributes is NOT
    consulted (consensus-steelman-posture §3.1: assertion is SOT; entity blob
    is a derived cache; audits MUST NOT trust the blob).
    """
    sql = (
        "SELECT entity_id, attributes, evidence_uris FROM assertions "
        "WHERE superseded_by IS NULL "
        "  AND attributes IS NOT NULL "
        "  AND entity_id LIKE 'decision:%' "
        "  AND json_extract(attributes, '$.consensus_disposition') = 'panel'"
    )
    params: tuple[Any, ...] = ()
    if subject:
        sql += " AND entity_id = ?"
        params = (subject,)
    rows = query(conn, sql, params)
    out: list[tuple[str, dict[str, Any], list[str]]] = []
    for row in rows:
        attrs = _parse_attributes(row.get("attributes"))
        if attrs and _is_material_panel(attrs):
            out.append(
                (
                    row["entity_id"],
                    attrs,
                    _parse_evidence_uris(row.get("evidence_uris")),
                )
            )
    return out


def detect_panel_disposition_incomplete(
    conn, subject: str | None = None
) -> list[dict[str, Any]]:
    """Flag material ``panel`` decisions missing Guard 3 fields or execution URIs.

    Reads from assertion.attributes (SOT) — entity.attributes is not consulted.
    """
    findings: list[dict[str, Any]] = []
    for entity_id, attrs, evidence_uris in _material_panel_rows(conn, subject):
        reasons: list[str] = list(validate_panel_assert_attributes(attrs))
        exec_count = count_execution_evidence_uris(evidence_uris)
        if exec_count < MIN_EXECUTION_EVIDENCE_URIS:
            reasons.append(
                f"confirmed assertion needs >= {MIN_EXECUTION_EVIDENCE_URIS} "
                f"execution: evidence_uris (found {exec_count})"
            )
        if not reasons:
            continue
        detail = (
            f"decision '{entity_id}' has consensus_disposition=panel (material) but "
            f"panel stamp is incomplete: {'; '.join(reasons)}. "
            "Bind via agent_seat.panel_dispatch.build_panel_assert_attributes; "
            "pass attributes=... to cortex assert (not entity_update). "
            "See agent-skills/consensus-steelman-posture.md §3.1."
        )
        findings.append(_finding("panel_disposition_incomplete", entity_id, detail))
    return findings


def detect_panel_falsifier_phase3_metric(
    conn, subject: str | None = None
) -> list[dict[str, Any]]:
    """Emit info metric when N>=20 material panel decisions lack lead adjudication artifact.

    Reads from assertion.attributes (SOT) — entity.attributes is not consulted.
    """
    cohort = _material_panel_rows(conn, subject)
    n = len(cohort)
    if n < MIN_MATERIAL_PANEL_COHORT:
        return []
    missing = 0
    for _eid, attrs, _uris in cohort:
        artifact = read_adjudication_artifact(attrs)
        if not artifact or not str(artifact).strip():
            missing += 1
    if missing == 0:
        return []
    fraction = missing / n
    detail = (
        f"Phase-3 falsifier metric (assertion 12858): {missing}/{n} material panel "
        f"decisions lack panel_adjudication_artifact (fraction={fraction:.3f}). "
        "Decisive falsifier for panel-by-omission closure."
    )
    return [
        _finding(
            "panel_falsifier_phase3_metric",
            "graph:material-panel-cohort",
            detail,
        )
    ]


__all__ = [
    "detect_panel_disposition_incomplete",
    "detect_panel_falsifier_phase3_metric",
]
