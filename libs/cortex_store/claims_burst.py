"""Claims burst v0 — vocabulary-scoped assertion retrieval with enrich-on-read.

Read-only: derives action-typed predicates from claim text at query time;
never writes predicate_form back to assertion rows. Every response carries a
``disclosure`` object accounting for scanned, returned, and dropped rows.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime

from predicate_form.action_enrichment import (
    DERIVATION_SOURCE,
    enrich_action_predicate_from_claim_with_reason,
)
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
    BURST_DROP_ID_CAP,
    BurstClaimItem,
    BurstDisclosure,
    BurstDropGroup,
    BurstDropReason,
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


def _burst_rank_key(item: BurstClaimItem, observed_at: datetime) -> tuple[int, float]:
    """Lexicographic terminal-first rank: terminal rows precede all non-terminal, then recency."""
    return (1 if item.terminal else 0, observed_at.timestamp())


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


def _drop_reason_from_token(token: str | None) -> BurstDropReason:
    if token is None:
        return BurstDropReason.detector_no_match
    return BurstDropReason(token)


def _enrich_row(
    row: dict,
    *,
    enrichment_domain: str | None = None,
) -> tuple[BurstClaimItem, ActionPredicate] | tuple[None, BurstDropReason]:
    preview, reason = enrich_action_predicate_from_claim_with_reason(
        row.get("claim") or "",
        row.get("entity_id") or "",
        assertion_id=int(row["id"]),
        valid_from=row.get("valid_from"),
        epistemic_state=_epistemic_state(row),
        domain=enrichment_domain,
    )
    if preview is None:
        return None, _drop_reason_from_token(reason)
    pred = parse_action_predicate(
        preview.predicate_form,
        assertion_id=int(row["id"]),
        epistemic_state=preview.epistemic_state,
    )
    if pred is None:
        return None, BurstDropReason.predicate_unparseable
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


def _build_disclosure(
    *,
    rows_scanned: int,
    claims: list[BurstClaimItem],
    drops_by_reason: dict[BurstDropReason, list[int]],
    request: ClaimsBurstRequest,
    vocab: set[str],
) -> BurstDisclosure:
    drop_groups: list[BurstDropGroup] = []
    for reason in sorted(drops_by_reason, key=lambda item: item.value):
        ids = drops_by_reason[reason]
        count = len(ids)
        sorted_ids = sorted(ids)
        capped_ids = sorted_ids[:BURST_DROP_ID_CAP]
        drop_groups.append(
            BurstDropGroup(
                reason=reason,
                count=count,
                assertion_ids=capped_ids,
                assertion_ids_truncated=count > len(capped_ids),
            )
        )
    rows_dropped_total = sum(group.count for group in drop_groups)
    return BurstDisclosure(
        rows_scanned=rows_scanned,
        rows_returned=len(claims),
        rows_dropped_total=rows_dropped_total,
        drops=drop_groups,
        vocabulary_requested=list(request.vocabulary),
        vocabulary_accepted=sorted(vocab),
        vocabulary_rejected=sorted(set(request.vocabulary) - vocab),
        detector_version=DERIVATION_SOURCE,
        disclosure_version=1,
    )


def burst_claims(
    conn,
    request: ClaimsBurstRequest,
    *,
    enrichment_domain: str | None = None,
) -> ClaimsBurstResponse:
    """Run vocabulary-scoped burst with enrich-on-read disclosure accounting."""
    vocab = {term for term in request.vocabulary if term in ACTION_VOCAB_V0}
    rows = _fetch_scope_rows(conn, request.scope_entity_ids)

    enriched: list[tuple[BurstClaimItem, datetime]] = []
    stored_preds: list[ActionPredicate] = []
    drops_by_reason: dict[BurstDropReason, list[int]] = defaultdict(list)

    for row in rows:
        enriched_row = _enrich_row(row, enrichment_domain=enrichment_domain)
        if enriched_row[0] is None:
            drops_by_reason[enriched_row[1]].append(int(row["id"]))
            continue
        item, pred = enriched_row
        if item.action not in vocab:
            drops_by_reason[BurstDropReason.action_out_of_vocabulary].append(int(row["id"]))
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

    disclosure = _build_disclosure(
        rows_scanned=len(rows),
        claims=claims,
        drops_by_reason=drops_by_reason,
        request=request,
        vocab=vocab,
    )

    return ClaimsBurstResponse(
        vocabulary=list(request.vocabulary),
        scope_entity_ids=list(request.scope_entity_ids),
        mode=request.mode,
        claims=claims,
        contradiction_pairs=contradiction_pairs,
        disclosure=disclosure,
    )
