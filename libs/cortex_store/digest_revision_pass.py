"""Digest revision pass — stages supersede/retract proposals on changed-sha re-talk."""

from __future__ import annotations

import uuid
from typing import Any

from .digest_anchor_loader import load_active_assertions_for_anchor
from .digest_claim_proposals import build_claim_proposals
from .digest_ledger import (
    compute_entry_content_sha256,
    lookup_effective_watermark,
    lookup_latest_for_anchor,
    lookup_pending_staged_batch,
)
from .digest_ledger import (
    write as ledger_write,
)
from .events_digest import digest_staged
from .journal_digest_revision_extract import extract_revision_decisions
from .journal_digest_verify import verify_claim_batch
from .models import StagingProposalCreate
from .routes.staging import create_staging_batch_on_conn


def _stage_revision_proposals(
    conn,
    *,
    revision_batch: dict[str, Any],
    entry_anchor: str,
    journal_entity_id: str,
    journal_uri: str,
    source_uri: str,
) -> tuple[list[StagingProposalCreate], list[int], list[int], list[int]]:
    """Build staging rows from revision decisions."""
    proposals: list[StagingProposalCreate] = []
    carried_forward: list[int] = []
    supersede_targets: list[int] = []
    retract_targets: list[int] = []

    prior_by_id = {
        int(p["id"]): p for p in revision_batch.get("prior_assertions") or []
    }

    for decision in revision_batch.get("decisions") or []:
        prior_id = int(decision["prior_id"])
        kind = decision["decision"]
        verbatim = decision.get("verbatim_evidence") or ""
        prior = prior_by_id.get(prior_id)
        if prior is None:
            continue

        if kind == "keep":
            carried_forward.append(prior_id)
            continue

        if kind == "remove":
            retract_targets.append(prior_id)
            proposals.append(
                StagingProposalCreate(
                    source_uri=source_uri,
                    proposal_type="assertion",
                    proposal_action="remove",
                    target_id=str(prior_id),
                    proposal_json={
                        "reason": verbatim or "operator re-talk removal",
                        "evidence": verbatim,
                    },
                )
            )
            continue

        if kind == "revise":
            successor = decision.get("successor") or {}
            claim_text = successor.get("claim") or prior.get("claim")
            derivation_type, confidence = _derive_from_successor(successor, prior)
            supersede_targets.append(prior_id)
            proposals.append(
                StagingProposalCreate(
                    source_uri=source_uri,
                    proposal_type="assertion",
                    proposal_action="revise",
                    target_id=str(prior_id),
                    proposal_json={
                        "entity_id": prior.get("entity_id"),
                        "claim": claim_text,
                        "confidence": confidence,
                        "derivation_type": derivation_type,
                        "valid_from": prior.get("valid_from"),
                        "is_atomic": True,
                        "is_decontextualized": True,
                        "evidence": verbatim,
                        "evidence_uris": prior.get("evidence_uris") or [],
                        "reasoning_summary": f"digest-revision:{entry_anchor}#supersedes:{prior_id}",
                    },
                )
            )

    for index, claim in enumerate(revision_batch.get("adds") or []):
        claim_proposals, _, _ = build_claim_proposals(
            conn,
            claim=claim,
            claim_index=index,
            entry_anchor=entry_anchor,
            journal_entity_id=journal_entity_id,
            journal_uri=journal_uri,
        )
        proposals.extend(claim_proposals)

    return proposals, carried_forward, supersede_targets, retract_targets


def _derive_from_successor(
    successor: dict[str, Any], prior: dict[str, Any]
) -> tuple[str, str]:
    from .digest_ledger import map_p_class_to_derivation_confidence

    p_class = successor.get("p_class")
    if isinstance(p_class, str) and p_class.strip():
        return map_p_class_to_derivation_confidence(p_class)
    return (
        str(prior.get("derivation_type") or "inference"),
        str(prior.get("confidence") or "believed"),
    )


def run_revision_pass(
    *,
    journal_entity_id: str,
    entry_anchor: str,
    entry_text: str,
    journal_uri: str | None = None,
) -> dict[str, Any]:
    """Changed-sha revision pass: load → delta extract → stage → ledger (staged)."""
    content_sha = compute_entry_content_sha256(entry_text)
    source_uri = journal_uri or f"cortex://journal/{journal_entity_id}"

    from .db import cortex_conn

    with cortex_conn() as conn:
        effective = lookup_effective_watermark(conn, journal_entity_id, entry_anchor)
        if effective is None:
            return {
                "status": "anomaly",
                "reason": "integrity_no_prior_ledger",
                "entry_anchor": entry_anchor,
            }

        pending = lookup_pending_staged_batch(
            conn, journal_entity_id, entry_anchor, content_sha
        )
        if pending is not None:
            return {
                "status": "revision_pending",
                "reason": "batch_pending_approval",
                "journal_entity_id": journal_entity_id,
                "entry_anchor": entry_anchor,
                "content_sha256": content_sha,
                "ledger_id": pending["id"],
                "staging_batch_id": pending.get("staging_batch_id"),
                "emitted_ids": pending.get("emitted_ids") or [],
            }

        priors = load_active_assertions_for_anchor(conn, entry_anchor)
        if not priors:
            return {
                "status": "anomaly",
                "reason": "integrity_no_prior_assertions",
                "entry_anchor": entry_anchor,
                "prior_ledger_id": effective.get("id"),
            }

        prior_sha = effective.get("content_sha256")
        revision_batch = extract_revision_decisions(
            entry_text,
            entry_anchor=entry_anchor,
            journal_uri=source_uri,
            prior_assertions=priors,
        )
        if revision_batch is None:
            return {
                "error": "revision_extract_failed",
                "code": "digest_revision_extract_failed",
                "entry_anchor": entry_anchor,
            }

        adds_only = {
            "entry_anchor": entry_anchor,
            "journal_uri": source_uri,
            "claims": revision_batch.get("adds") or [],
            "verify_verdicts": {},
        }
        if adds_only["claims"]:
            verified = verify_claim_batch(
                entry_text, adds_only, entry_anchor=entry_anchor
            )
            if verified is None:
                return {"error": "verify_failed", "code": "digest_verify_failed"}
            revision_batch["adds"] = verified.get("claims") or []

        proposals, carried, superseded, retracted = _stage_revision_proposals(
            conn,
            revision_batch=revision_batch,
            entry_anchor=entry_anchor,
            journal_entity_id=journal_entity_id,
            journal_uri=source_uri,
            source_uri=source_uri,
        )

        staging_batch_id = str(uuid.uuid4())
        emitted_ids: list[Any] = []
        if proposals:
            emitted_ids = create_staging_batch_on_conn(conn, proposals)

        latest = lookup_latest_for_anchor(conn, journal_entity_id, entry_anchor)
        revision_of = int(latest["id"]) if latest else None

        ledger_id = ledger_write(
            conn,
            journal_entity_id=journal_entity_id,
            entry_anchor=entry_anchor,
            content_sha256=content_sha,
            emitted_ids=emitted_ids,
            staging_batch_id=staging_batch_id,
            verify_verdicts={"revision": True, "prior_sha256": prior_sha},
            revision_of=revision_of,
            status="staged",
            superseded_ids=superseded,
            retracted_ids=retracted,
            carried_forward_ids=carried,
        )
        conn.commit()

    digest_staged(
        journal_entity_id=journal_entity_id,
        entry_anchor=entry_anchor,
        status="revision_staged",
        ledger_id=ledger_id,
        staging_batch_id=staging_batch_id,
    )

    return {
        "status": "revision_staged",
        "reason": "content_sha_changed",
        "journal_entity_id": journal_entity_id,
        "entry_anchor": entry_anchor,
        "content_sha256": content_sha,
        "prior_sha256": prior_sha,
        "prior_ledger_id": revision_of,
        "staging_batch_id": staging_batch_id,
        "ledger_id": ledger_id,
        "emitted_ids": emitted_ids,
        "carried_forward_ids": carried,
        "superseded_ids": superseded,
        "retracted_ids": retracted,
        "revision_flags": revision_batch.get("flags") or [],
    }


__all__ = ["run_revision_pass"]
