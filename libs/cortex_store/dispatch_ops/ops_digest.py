"""Digest op — watermark, extract, verify, attach, dedup, stage, ledger."""

from __future__ import annotations

import uuid
from typing import Any

from ..db import cortex_conn
from ..digest_claim_proposals import build_claim_proposals
from ..digest_dedup import enrich_claim_batch_dedup_candidates
from ..digest_ledger import (
    compute_entry_content_sha256,
    lookup,
    lookup_effective_watermark,
)
from ..digest_ledger import (
    write as ledger_write,
)
from ..digest_revision_pass import run_revision_pass
from ..digest_segment import aggregate_auto_segment_digest
from ..events_digest import digest_extract, digest_staged, digest_verify
from ..digest_extract_backend import extract_claims
from ..journal_digest_verify import verify_claim_batch
from ..models import StagingProposalCreate
from ..routes.staging import create_staging_batch_on_conn


def _digest_one(
    *,
    journal_entity_id: str,
    entry_anchor: str,
    entry_text: str,
    journal_uri: str | None = None,
) -> dict[str, Any]:
    """Single-section digest: watermark → extract → verify → stage → ledger."""

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

        latest = lookup_effective_watermark(conn, journal_entity_id, entry_anchor)
        if latest is not None and latest["content_sha256"] != content_sha:
            digest_staged(
                journal_entity_id=journal_entity_id,
                entry_anchor=entry_anchor,
                status="revision",
                ledger_id=int(latest["id"]),
            )
            return run_revision_pass(
                journal_entity_id=journal_entity_id,
                entry_anchor=entry_anchor,
                entry_text=entry_text,
                journal_uri=journal_uri,
            )

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
            claim_proposals, skipped, flagged = build_claim_proposals(
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


def _op_digest(
    journal_entity_id: str | None = None,
    entry_anchor: str | None = None,
    entry_text: str | None = None,
    journal_uri: str | None = None,
    auto_segment: bool = False,
    entry_date: str | None = None,
    **_: object,
) -> dict[str, Any]:
    """Watermark → extract → verify → attach/map → dedup → stage → ledger."""
    if not journal_entity_id:
        return {
            "error": "journal_entity_id is required",
            "code": "missing_journal_entity_id",
        }
    if not entry_text:
        return {"error": "entry_text is required", "code": "missing_entry_text"}

    if auto_segment:
        if not entry_date:
            return {
                "error": "entry_date is required when auto_segment is true",
                "code": "missing_entry_date",
            }
        return aggregate_auto_segment_digest(
            _digest_one,
            journal_entity_id=journal_entity_id,
            entry_text=entry_text,
            entry_date=entry_date,
            journal_uri=journal_uri,
        )

    if not entry_anchor:
        return {"error": "entry_anchor is required", "code": "missing_entry_anchor"}

    return _digest_one(
        journal_entity_id=journal_entity_id,
        entry_anchor=entry_anchor,
        entry_text=entry_text,
        journal_uri=journal_uri,
    )
