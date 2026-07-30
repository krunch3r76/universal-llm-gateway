"""Action-typed predicate enrichment templates (slice 2).

Rule-based extraction from claim text for denied/request/granted/pending
patterns. Dry-run only — never writes to live assertion rows.
"""

from __future__ import annotations

from dataclasses import dataclass

from .action_detection import (
    SegmentMatch,
    match_claim_segments_with_reason,
    truncate_claim_excerpt,
)
from .action_patterns import ACTION_VOCAB_BY_DOMAIN, ACTION_VOCAB_V0
from .action_vocabulary import ActionPredicate, party_for_entity

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


def _vocab_for_domain(domain: str | None) -> frozenset[str]:
    if domain is None:
        return ACTION_VOCAB_V0
    return ACTION_VOCAB_BY_DOMAIN.get(domain, frozenset())


def _preview_from_match(
    match: SegmentMatch,
    *,
    entity_id: str,
    assertion_id: int | None,
    epistemic_state: str | None,
    domain: str | None = None,
) -> EnrichmentPreview:
    party = party_for_entity(entity_id, domain=domain)
    assert party is not None

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


def enrich_action_predicate_from_claim_with_reason(
    claim: str,
    entity_id: str,
    *,
    assertion_id: int | None = None,
    observed_at: str | None = None,
    valid_from: str | None = None,
    epistemic_state: str | None = None,
    domain: str | None = None,
) -> tuple[EnrichmentPreview | None, str | None]:
    """Extract an action-typed predicate preview and an optional drop-reason token.

    Propagates detector reasons from ``match_claim_segments_with_reason``.
    Returns ``party_underivable`` when the entity id lacks a party slug, and
    ``detector_action_unknown`` when the matched action is outside the domain
    vocabulary (or union vocabulary when ``domain`` is None). On success the
    reason is ``None``.
    """
    del observed_at  # never substitute ingest timestamps for disposition dates
    match, reason = match_claim_segments_with_reason(
        claim,
        valid_from=valid_from,
        domain=domain,
    )
    if match is None:
        return None, reason

    party = party_for_entity(entity_id, domain=domain)
    if not party:
        return None, "party_underivable"
    if match.action not in _vocab_for_domain(domain):
        return None, "detector_action_unknown"

    return (
        _preview_from_match(
            match,
            entity_id=entity_id,
            assertion_id=assertion_id,
            epistemic_state=epistemic_state,
            domain=domain,
        ),
        None,
    )


def enrich_action_predicate_from_claim(
    claim: str,
    entity_id: str,
    *,
    assertion_id: int | None = None,
    observed_at: str | None = None,
    valid_from: str | None = None,
    epistemic_state: str | None = None,
    domain: str | None = None,
) -> EnrichmentPreview | None:
    """Extract an action-typed predicate_form preview from claim text (read-only)."""
    preview, _reason = enrich_action_predicate_from_claim_with_reason(
        claim,
        entity_id,
        assertion_id=assertion_id,
        observed_at=observed_at,
        valid_from=valid_from,
        epistemic_state=epistemic_state,
        domain=domain,
    )
    return preview


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
