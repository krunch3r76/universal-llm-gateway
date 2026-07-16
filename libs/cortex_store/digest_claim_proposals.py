"""Build staging proposals from verified digest claims."""

from __future__ import annotations

import re
from typing import Any

from .digest_attach import digest_resolve_attach, parse_attach_hint
from .digest_dedup import resolve_staging_dedup_skip
from .digest_ledger import derive_valid_from_hint, map_p_class_to_derivation_confidence
from .models import StagingProposalCreate

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


def build_claim_proposals(
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
