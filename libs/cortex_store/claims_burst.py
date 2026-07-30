"""Claims burst v0 — vocabulary-scoped assertion retrieval with enrich-on-read.

Read-only: derives action-typed predicates from claim text at query time;
never writes predicate_form back to assertion rows.
"""

from __future__ import annotations

from datetime import UTC, datetime

from predicate_form.action_enrichment import enrich_action_predicate_from_claim
from predicate_form.action_vocabulary import (
    ACTION_VOCAB_V0,
    TERMINAL_FUNCTORS,
    ActionPredicate,
    parse_action_predicate,
    party_from_entity_id,
)
from predicate_form.collision import Contradiction, detect_contradictions

from .db import query
from .models.claims_burst import (
    BurstClaimItem,
    ClaimsBurstRequest,
    ClaimsBurstResponse,
    ContradictionPairItem,
)

_BURST_COLS = (
    "id, entity_id, claim, review_status, confidence, observed_at, valid_from, "
    "predicate_form, superseded_by"
)


def _parse_observed_at(raw: str | None, valid_from: str | None) -> datetime:
    for candidate in (raw, valid_from):
        if not candidate:
            continue
        text = candidate.strip()
        if not text:
            continue
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            if len(text) >= 10:
                try:
                    parsed = datetime.fromisoformat(text[:10])
                except ValueError:
                    continue
            else:
                continue
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)
    return datetime.min.replace(tzinfo=UTC)


def _burst_rank_key(item: BurstClaimItem, observed_at: datetime) -> tuple[float, float]:
    terminal_weight = 1.0 if item.terminal else 0.1
    recency_epoch = observed_at.timestamp()
    return (terminal_weight * 1000.0 + recency_epoch / 86400.0, recency_epoch)


def _epistemic_state(row: dict) -> str | None:
    review = row.get("review_status")
    if review:
        return str(review)
    confidence = row.get("confidence")
    if confidence:
        return str(confidence)
    return None


def _fetch_scope_rows(conn, entity_ids: list[str]) -> list[dict]:
    if not entity_ids:
        return []
    placeholders = ",".join("?" for _ in entity_ids)
    sql = (
        f"SELECT {_BURST_COLS} FROM assertions "
        f"WHERE entity_id IN ({placeholders}) AND superseded_by IS NULL "
        "ORDER BY observed_at DESC, id DESC"
    )
    return query(conn, sql, tuple(entity_ids))


def _enrich_row(row: dict) -> tuple[BurstClaimItem, ActionPredicate] | None:
    preview = enrich_action_predicate_from_claim(
        row.get("claim") or "",
        row.get("entity_id") or "",
        assertion_id=int(row["id"]),
        valid_from=row.get("valid_from"),
        epistemic_state=_epistemic_state(row),
    )
    if preview is None:
        return None
    pred = parse_action_predicate(
        preview.predicate_form,
        assertion_id=int(row["id"]),
        epistemic_state=preview.epistemic_state,
    )
    if pred is None:
        return None
    item = BurstClaimItem(
        assertion_id=int(row["id"]),
        claim=row.get("claim") or "",
        predicate_form=preview.predicate_form,
        epistemic_state=preview.epistemic_state,
        terminal=preview.functor in TERMINAL_FUNCTORS,
        entity_id=preview.entity_id,
        functor=preview.functor,
        action=preview.action,
        party=preview.party,
        derivation=preview.source,
        claim_excerpt=preview.claim_excerpt,
    )
    return item, pred


def _parties_for_scope(scope_entity_ids: list[str]) -> list[str]:
    parties: list[str] = []
    seen: set[str] = set()
    for entity_id in scope_entity_ids:
        party = party_from_entity_id(entity_id)
        if not party or party in seen:
            continue
        seen.add(party)
        parties.append(party)
    return parties


def _build_contradiction_pairs(
    vocabulary: list[str],
    scope_entity_ids: list[str],
    stored: list[ActionPredicate],
) -> list[ContradictionPairItem]:
    parties = _parties_for_scope(scope_entity_ids)
    proposed: list[ActionPredicate] = []
    for action in vocabulary:
        if action not in ACTION_VOCAB_V0:
            continue
        for party in parties:
            proposed.append(
                ActionPredicate(
                    functor="request",
                    action=action,
                    party=party,
                )
            )
    pairs: list[ContradictionPairItem] = []
    for hit in detect_contradictions(proposed, stored):
        if not isinstance(hit, Contradiction):
            continue
        pairs.append(
            ContradictionPairItem(
                proposed_predicate_form=hit.proposed.to_predicate_form(),
                proposed_functor=hit.proposed.functor,
                blocking_assertion_id=hit.blocking_assertion_id,
                blocking_predicate_form=hit.blocking_predicate_form,
                reason=hit.reason,
            )
        )
    return pairs


def burst_claims(conn, request: ClaimsBurstRequest) -> ClaimsBurstResponse:
    """Run vocabulary-scoped burst with enrich-on-read and terminal-first ranking."""
    vocab = {term for term in request.vocabulary if term in ACTION_VOCAB_V0}
    rows = _fetch_scope_rows(conn, request.scope_entity_ids)

    enriched: list[tuple[BurstClaimItem, datetime]] = []
    stored_preds: list[ActionPredicate] = []

    for row in rows:
        enriched_row = _enrich_row(row)
        if enriched_row is None:
            continue
        item, pred = enriched_row
        if item.action not in vocab:
            continue
        observed = _parse_observed_at(row.get("observed_at"), row.get("valid_from"))
        enriched.append((item, observed))
        stored_preds.append(pred)

    enriched.sort(key=lambda pair: _burst_rank_key(pair[0], pair[1]), reverse=True)
    claims = [item for item, _ in enriched]

    contradiction_pairs: list[ContradictionPairItem] = []
    if request.include_contradictions:
        contradiction_pairs = _build_contradiction_pairs(
            request.vocabulary,
            request.scope_entity_ids,
            stored_preds,
        )

    return ClaimsBurstResponse(
        vocabulary=list(request.vocabulary),
        scope_entity_ids=list(request.scope_entity_ids),
        mode=request.mode,
        claims=claims,
        contradiction_pairs=contradiction_pairs,
    )
