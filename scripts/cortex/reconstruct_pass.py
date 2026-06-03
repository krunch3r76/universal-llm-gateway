"""Attach-or-flag pass runner for provenance reconstruct."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from transport_utils import DEFAULT_CORTEX_URL, make_sync_client

from reconstruct_constants import MARKER, REVIEW_NOTES
from reconstruct_candidates import load_candidates
from reconstruct_models import Candidate, Outcome
from reconstruct_uri import locate_source


def attach(client: Any, row: Candidate, uri: str, session_id: str, agent: str) -> int:
    uris = row.evidence_uris if row.evidence_uris else [uri]
    if uri not in uris:
        uris = [uri] + [u for u in uris if u != uri]
    body = {
        "old_assertion_id": row.id,
        "entity_id": row.entity_id,
        "claim": row.claim,
        "confidence": row.confidence,
        "evidence": row.evidence,
        "evidence_uris": uris,
        "session_id": session_id,
        "agent": agent,
        "seeded_by": MARKER,
        "acknowledge_audit_gaps": ["inference_confirmed"],
    }
    r = client.post("/assertions/supersede", json=body)
    r.raise_for_status()
    return int(r.json()["new"]["id"])


def flag(client: Any, assertion_id: int) -> None:
    body = {
        "review_status": "staged",
        "reviewer": MARKER,
        "review_notes": REVIEW_NOTES,
    }
    r = client.patch(f"/assertions/{assertion_id}", json=body)
    r.raise_for_status()


def run_pass(
    *,
    db_path: Path,
    entity_ids: list[str] | None,
    limit: int | None,
    live: bool,
    session_id: str,
    agent: str,
) -> dict[str, Any]:
    candidates = load_candidates(db_path, entity_ids, limit)
    outcomes: list[Outcome] = []
    counts = {"attach": 0, "flag": 0, "skip": 0, "near_miss": 0}

    with make_sync_client(DEFAULT_CORTEX_URL, timeout=60.0) as client:
        for row in candidates:
            uri, near = locate_source(row)
            if uri:
                if not live:
                    outcomes.append(
                        Outcome(row.id, row.entity_id, "attach", "dry-run", uri, near)
                    )
                    counts["attach"] += 1
                    continue
                try:
                    new_id = attach(client, row, uri, session_id, agent)
                    outcomes.append(
                        Outcome(
                            row.id,
                            row.entity_id,
                            "attach",
                            f"superseded → {new_id}",
                            uri,
                            near,
                        )
                    )
                    counts["attach"] += 1
                except Exception as exc:
                    outcomes.append(
                        Outcome(
                            row.id,
                            row.entity_id,
                            "flag",
                            f"attach failed: {exc}",
                            None,
                            near or str(exc),
                        )
                    )
                    flag(client, row.id)
                    counts["flag"] += 1
                    if near:
                        counts["near_miss"] += 1
            else:
                if live:
                    flag(client, row.id)
                outcomes.append(
                    Outcome(
                        row.id, row.entity_id, "flag", "no locatable source", None, near
                    )
                )
                counts["flag"] += 1
                if near:
                    counts["near_miss"] += 1

    by_entity: dict[str, dict[str, int]] = {}
    for o in outcomes:
        bucket = by_entity.setdefault(o.entity_id, {"attach": 0, "flag": 0})
        bucket[o.action] = bucket.get(o.action, 0) + 1

    return {
        "total": len(candidates),
        "counts": counts,
        "by_entity": by_entity,
        "outcomes": outcomes,
        "live": live,
    }
