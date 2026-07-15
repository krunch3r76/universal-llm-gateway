"""Digest op — watermark, extract, verify, attach, dedup, stage, ledger."""

from __future__ import annotations

import re
import uuid
from typing import Any

from ..db import cortex_conn
from ..digest_attach import digest_resolve_attach, parse_attach_hint
from ..digest_dedup import (
    enrich_claim_batch_dedup_candidates,
    resolve_staging_dedup_skip,
)
from ..digest_ledger import (
    compute_entry_content_sha256,
    derive_valid_from_hint,
    lookup,
    lookup_latest_for_anchor,
    map_p_class_to_derivation_confidence,
)
from ..digest_ledger import (
    write as ledger_write,
)
from ..events_digest import digest_extract, digest_staged, digest_verify
from ..journal_digest_extract import extract_claims
from ..journal_digest_verify import verify_claim_batch
from ..models import StagingProposalCreate
from ..routes.staging import create_staging_batch_on_conn

_DEADLINE_WORDS = ("deadline", "due date", "appointment", "payment due", "must pay by")


def _claim_is_deadline(claim: dict[str, Any]) -> bool:
    flags = claim.get("flags") or []
    return (
        "deadline_conflict" in flags
        or "deadline" in str(claim.get("evidence_anchor", "")).lower()
        or (
            derive_valid_from_hint(claim) is not None
            and any(w in str(claim.get("claim", "")).lower() for w in _DEADLINE_WORDS)
        )
    )


def _stage_deadline_proposals(
    proposals: list[StagingProposalCreate],
    *,
    claim: dict[str, Any],
    claim_index: int,
    entry_anchor: str,
    entity_id: str,
    source_uri: str,
) -> None:
    due_date = derive_valid_from_hint(claim)
    if not due_date:
        return
    slug = re.sub(r"[^a-z0-9]+", "-", entry_anchor.lower()).strip("-")
    deadline_id = f"deadline:{slug}:{due_date}:{claim_index}"
    proposals.append(
        StagingProposalCreate(
            source_uri=source_uri,
            proposal_type="entity",
            proposal_action="add",
            proposal_json={
                "id": deadline_id,
                "type": "deadline",
                "name": f"Deadline {due_date}",
                "attributes": {"due_date": due_date},
                "source_uri": source_uri,
            },
        )
    )
    proposals.append(
        StagingProposalCreate(
            source_uri=source_uri,
            proposal_type="relationship",
            proposal_action="add",
            proposal_json={
                "source_id": deadline_id,
                "target_id": entity_id,
                "type_id": "deadline_for",
                "valid_from": due_date,
                "source_uri": source_uri,
            },
        )
    )


def _build_claim_proposals(
    conn,
    *,
    claim: dict[str, Any],
    claim_index: int,
    entry_anchor: str,
    journal_entity_id: str,
    journal_uri: str | None,
) -> tuple[list[StagingProposalCreate], list[str], list[int]]:
    proposals: list[StagingProposalCreate] = []
    skipped: list[str] = []
    flagged: list[int] = []
    source_uri = journal_uri or f"cortex://journal/{journal_entity_id}"
    is_prose = claim.get("canonicality") == "prose"

    attach_hint = claim.get("attach_hint")
    resolved_id, _search_hits = digest_resolve_attach(conn, attach_hint)
    entity_id = resolved_id
    deferred_entity: StagingProposalCreate | None = None

    if attach_hint and resolved_id is None and not is_prose:
        proposed_id, entity_type, display_name = parse_attach_hint(attach_hint)
        entity_id = proposed_id
        deferred_entity = StagingProposalCreate(
            source_uri=source_uri,
            proposal_type="entity",
            proposal_action="add",
            proposal_json={
                "id": proposed_id,
                "type": entity_type,
                "name": display_name,
                "source_uri": source_uri,
            },
        )

    if not entity_id:
        entity_id = journal_entity_id

    if is_prose:
        skipped.append(f"skipped_prose:{claim_index}")
    else:
        skip_id = resolve_staging_dedup_skip(
            conn,
            claim=claim,
            resolved_id=resolved_id,
            source_uri=source_uri,
            entity_id=entity_id,
        )
        if skip_id is not None:
            skipped.append(f"assertion:{skip_id}")
        else:
            if deferred_entity is not None:
                proposals.append(deferred_entity)
            derivation_type, confidence = map_p_class_to_derivation_confidence(
                claim["p_class"]
            )
            proposals.append(
                StagingProposalCreate(
                    source_uri=source_uri,
                    proposal_type="assertion",
                    proposal_action="add",
                    proposal_json={
                        "entity_id": entity_id,
                        "claim": claim["claim"],
                        "confidence": confidence,
                        "derivation_type": derivation_type,
                        "valid_from": derive_valid_from_hint(claim),
                        "is_atomic": True,
                        "is_decontextualized": True,
                        "evidence_uris": [source_uri] if source_uri else [],
                        "reasoning_summary": f"digest:{entry_anchor}#{claim_index}",
                    },
                )
            )

    if _claim_is_deadline(claim):
        _stage_deadline_proposals(
            proposals,
            claim=claim,
            claim_index=claim_index,
            entry_anchor=entry_anchor,
            entity_id=entity_id,
            source_uri=source_uri,
        )

    if claim.get("verify_verdict") == "flag":
        flagged.append(claim_index)

    return proposals, skipped, flagged


def _op_digest(
    journal_entity_id: str | None = None,
    entry_anchor: str | None = None,
    entry_text: str | None = None,
    journal_uri: str | None = None,
    **_: object,
) -> dict[str, Any]:
    """Watermark → extract → verify → attach/map → dedup → stage → ledger."""
    if not journal_entity_id:
        return {
            "error": "journal_entity_id is required",
            "code": "missing_journal_entity_id",
        }
    if not entry_anchor:
        return {"error": "entry_anchor is required", "code": "missing_entry_anchor"}
    if not entry_text:
        return {"error": "entry_text is required", "code": "missing_entry_text"}

    content_sha = compute_entry_content_sha256(entry_text)
    with cortex_conn() as conn:
        prior = lookup(conn, journal_entity_id, entry_anchor, content_sha)
        if prior is not None:
            digest_staged(
                journal_entity_id=journal_entity_id,
                entry_anchor=entry_anchor,
                status="skipped",
                ledger_id=int(prior["id"]),
            )
            return {
                "status": "skipped",
                "reason": "watermark_match",
                "journal_entity_id": journal_entity_id,
                "entry_anchor": entry_anchor,
                "content_sha256": content_sha,
                "ledger_id": prior["id"],
            }

        latest = lookup_latest_for_anchor(conn, journal_entity_id, entry_anchor)
        if latest is not None and latest["content_sha256"] != content_sha:
            digest_staged(
                journal_entity_id=journal_entity_id,
                entry_anchor=entry_anchor,
                status="anomaly",
                ledger_id=int(latest["id"]),
            )
            return {
                "status": "anomaly",
                "reason": "content_sha_changed",
                "journal_entity_id": journal_entity_id,
                "entry_anchor": entry_anchor,
                "content_sha256": content_sha,
                "prior_sha256": latest["content_sha256"],
                "prior_ledger_id": latest["id"],
            }

    claim_batch = extract_claims(
        entry_text,
        entry_anchor=entry_anchor,
        journal_uri=journal_uri or "",
    )
    if claim_batch is None:
        return {"error": "extract_failed", "code": "digest_extract_failed"}

    digest_extract(
        journal_entity_id=journal_entity_id,
        entry_anchor=entry_anchor,
        claim_count=len(claim_batch.get("claims", [])),
    )

    source_uri = journal_uri or f"cortex://journal/{journal_entity_id}"
    with cortex_conn() as conn:
        claim_batch = enrich_claim_batch_dedup_candidates(
            conn,
            claim_batch,
            journal_uri=source_uri,
            journal_entity_id=journal_entity_id,
        )

    verified = verify_claim_batch(
        entry_text,
        claim_batch,
        entry_anchor=entry_anchor,
    )
    if verified is None:
        return {"error": "verify_failed", "code": "digest_verify_failed"}

    digest_verify(
        journal_entity_id=journal_entity_id,
        entry_anchor=entry_anchor,
        claim_count=len(verified.get("claims", [])),
    )

    proposals: list[StagingProposalCreate] = []
    skipped_dedups: list[str] = []
    flagged_indices: list[int] = []
    staged_counts = {"entity": 0, "assertion": 0, "relationship": 0}

    with cortex_conn() as conn:
        for index, claim in enumerate(verified.get("claims", [])):
            claim_proposals, skipped, flagged = _build_claim_proposals(
                conn,
                claim=claim,
                claim_index=index,
                entry_anchor=entry_anchor,
                journal_entity_id=journal_entity_id,
                journal_uri=journal_uri,
            )
            proposals.extend(claim_proposals)
            skipped_dedups.extend(skipped)
            flagged_indices.extend(flagged)

        staging_batch_id = str(uuid.uuid4())
        emitted_ids: list[Any] = []
        if proposals:
            row_ids = create_staging_batch_on_conn(conn, proposals)
            emitted_ids = row_ids
            for proposal, row_id in zip(proposals, row_ids, strict=True):
                staged_counts[proposal.proposal_type] = (
                    staged_counts.get(proposal.proposal_type, 0) + 1
                )

        verify_verdicts = verified.get("verify_verdicts") or {}
        ledger_id = ledger_write(
            conn,
            journal_entity_id=journal_entity_id,
            entry_anchor=entry_anchor,
            content_sha256=content_sha,
            emitted_ids=emitted_ids,
            staging_batch_id=staging_batch_id,
            verify_verdicts=verify_verdicts,
        )
        conn.commit()

    digest_staged(
        journal_entity_id=journal_entity_id,
        entry_anchor=entry_anchor,
        status="staged",
        ledger_id=ledger_id,
        staging_batch_id=staging_batch_id,
    )

    return {
        "status": "staged",
        "journal_entity_id": journal_entity_id,
        "entry_anchor": entry_anchor,
        "content_sha256": content_sha,
        "staging_batch_id": staging_batch_id,
        "ledger_id": ledger_id,
        "emitted_ids": emitted_ids,
        "staged_counts": staged_counts,
        "flagged_claim_indices": sorted(set(flagged_indices)),
        "skipped_dedups": skipped_dedups,
        "verify_verdicts": verify_verdicts,
    }
