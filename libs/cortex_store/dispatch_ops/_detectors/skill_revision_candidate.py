"""GRAPH_ONLY audit detector for parked skill-revision candidates.

Surfaces ``agent_skill`` (and guidance sibling) entities carrying
unadjudicated assertions whose claim text begins with
``CANDIDATE SKILL REVISION``. v1 matches claim-prefix only — structured
``attributes.candidate_kind`` / ``review_status=flagged`` capture is a
non-blocking follow-up for skill authors.
"""

from __future__ import annotations

from typing import Any

from ...db import query
from ._shared import _finding

_KIND = "agent_skill_revision_candidate_unadjudicated"
_CLAIM_PREFIX = "CANDIDATE SKILL REVISION%"


def detect_agent_skill_revision_candidate_unadjudicated(
    conn, subject: str | None = None
) -> list[dict[str, Any]]:
    """One finding per guidance entity with open revision-candidate assertions."""
    sql = (
        "SELECT e.id AS entity_id, a.id AS assertion_id, a.claim "
        "FROM entities e "
        "JOIN assertions a ON a.entity_id = e.id "
        "WHERE e.type IN ('agent_skill', 'rule', 'skill') "
        "AND a.claim LIKE ? "
        "AND a.superseded_by IS NULL "
        "AND (a.review_status IS NULL "
        "OR a.review_status NOT IN ('rejected', 'committed'))"
    )
    params: list[Any] = [_CLAIM_PREFIX]
    if subject:
        sql += " AND e.id = ?"
        params.append(subject)

    rows = query(conn, sql, tuple(params))
    by_entity: dict[str, list[tuple[int, str]]] = {}
    for row in rows:
        eid = row["entity_id"]
        by_entity.setdefault(eid, []).append(
            (int(row["assertion_id"]), str(row["claim"]))
        )

    findings: list[dict[str, Any]] = []
    for entity_id, candidates in sorted(by_entity.items()):
        parts = []
        for aid, claim in candidates:
            first_line = claim.split("\n", 1)[0].strip()
            parts.append(f"assertion {aid}: {first_line}")
        detail = (
            f"{len(candidates)} unadjudicated skill-revision candidate(s): "
            + "; ".join(parts)
        )
        findings.append(_finding(_KIND, entity_id, detail))
    return findings


__all__ = ["detect_agent_skill_revision_candidate_unadjudicated"]
