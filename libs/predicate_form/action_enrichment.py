"""Action-typed predicate enrichment templates (slice 2).

Rule-based extraction from claim text for denied/request/granted/pending
patterns. Dry-run only — never writes to live assertion rows.
"""

from __future__ import annotations

from dataclasses import dataclass

from .action_detection import match_claim_segments, truncate_claim_excerpt
from .action_vocabulary import ACTION_VOCAB_V0, ActionPredicate, party_from_entity_id

DERIVATION_SOURCE = "action_enrichment_template_v0"


@dataclass(frozen=True)
class EnrichmentPreview:
    assertion_id: int | None
    entity_id: str
    predicate_form: str
    functor: str
    action: str
    party: str
    epistemic_state: str | None
    source: str
    matched_segment: str | None = None
    claim_excerpt: str | None = None


def enrich_action_predicate_from_claim(
    claim: str,
    entity_id: str,
    *,
    assertion_id: int | None = None,
    observed_at: str | None = None,
    valid_from: str | None = None,
    epistemic_state: str | None = None,
) -> EnrichmentPreview | None:
    """Extract an action-typed predicate_form preview from claim text (read-only)."""
    del observed_at  # never substitute ingest timestamps for disposition dates
    match = match_claim_segments(claim, valid_from=valid_from)
    if match is None:
        return None

    party = party_from_entity_id(entity_id)
    if not party or match.action not in ACTION_VOCAB_V0:
        return None

    if match.functor == "request":
        pred = ActionPredicate(
            functor="request",
            action=match.action,
            party=party,
            assertion_id=assertion_id,
            epistemic_state=epistemic_state,
        )
    elif match.functor == "pending":
        pred = ActionPredicate(
            functor="pending",
            action=match.action,
            party=party,
            wo_id=match.wo_id,
            assertion_id=assertion_id,
            epistemic_state=epistemic_state,
        )
    else:
        pred = ActionPredicate(
            functor=match.functor,  # type: ignore[arg-type]
            action=match.action,
            party=party,
            date=match.date,
            assertion_id=assertion_id,
            epistemic_state=epistemic_state,
        )

    excerpt = truncate_claim_excerpt(match.segment)
    return EnrichmentPreview(
        assertion_id=assertion_id,
        entity_id=entity_id,
        predicate_form=pred.to_predicate_form(),
        functor=match.functor,
        action=match.action,
        party=party,
        epistemic_state=epistemic_state,
        source=DERIVATION_SOURCE,
        matched_segment=match.segment,
        claim_excerpt=excerpt,
    )


def dry_run_enrich_assertions(
    rows: list[dict],
) -> list[EnrichmentPreview]:
    """Dry-run enrichment over assertion-shaped dicts (claim + entity_id + id)."""
    previews: list[EnrichmentPreview] = []
    for row in rows:
        claim = row.get("claim") or ""
        entity_id = row.get("entity_id") or ""
        preview = enrich_action_predicate_from_claim(
            claim,
            entity_id,
            assertion_id=row.get("id"),
            valid_from=row.get("valid_from"),
            epistemic_state=row.get("review_status") or row.get("confidence"),
        )
        if preview is not None:
            previews.append(preview)
    return previews
