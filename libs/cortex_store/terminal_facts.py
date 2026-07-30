"""Terminal facts enrich-on-read for case/account hub entity_get (arc 6386 slice 5a).

Read-only: derives terminal predicates from hub-scoped assertions via
``burst_claims`` — no seat-supplied vocabulary or draft access.
"""

from __future__ import annotations

import sqlite3

from predicate_form.action_vocabulary import (
    ACTION_VOCAB_BY_DOMAIN,
    ACTION_VOCAB_V0,
    parse_action_predicate,
)

from . import scope_radiation
from .claims_burst import burst_claims
from .models.claims_burst import BurstClaimItem, ClaimsBurstRequest
from .models.terminal_facts import TerminalFactsBlock
from .scope_radiation import RadiatedScope, radiate_scope

TERMINAL_FACTS_CAP = 17
TERMINAL_FACTS_HOPS = 2
CLAIM_EXCERPT_MAX = 200
HUB_ID_PREFIXES = ("case:", "account:")
DETECTOR_VERSION = "action_enrichment_template_v0"


def is_terminal_facts_hub(entity_id: str) -> bool:
    """True when entity id uses a case or account hub prefix."""
    return entity_id.startswith(HUB_ID_PREFIXES)


def resolve_terminal_facts_scope(
    conn: sqlite3.Connection,
    entity_id: str,
) -> list[str]:
    """Radiated hub scope: root plus n-hop neighbours (no prefix or edge filter)."""
    if not is_terminal_facts_hub(entity_id):
        return [entity_id]
    scope = radiate_scope(conn, entity_id, hops=TERMINAL_FACTS_HOPS)
    return sorted(scope.hop_distances)


def radiate_terminal_facts_scope(
    conn: sqlite3.Connection,
    entity_id: str,
) -> RadiatedScope:
    """Full radiation envelope with hop distances, paths, and truncation flag."""
    if not is_terminal_facts_hub(entity_id):
        return RadiatedScope({entity_id: 0}, {entity_id: [entity_id]}, False)
    return radiate_scope(conn, entity_id, hops=TERMINAL_FACTS_HOPS)


def _disposition_date(item: BurstClaimItem) -> str | None:
    pred = parse_action_predicate(item.predicate_form, assertion_id=item.assertion_id)
    return pred.date if pred else None


def _compact_terminal_fact(item: BurstClaimItem) -> BurstClaimItem:
    excerpt = item.claim_excerpt or item.claim
    if len(excerpt) > CLAIM_EXCERPT_MAX:
        excerpt = excerpt[: CLAIM_EXCERPT_MAX - 1].rstrip() + "…"
    detector = item.derivation or DETECTOR_VERSION
    disposition_date = _disposition_date(item)
    return item.model_copy(
        update={
            "claim": excerpt,
            "claim_excerpt": None,
            "derivation": detector,
            "machine_derived": True,
            "detector_version": detector,
            "disposition_date": disposition_date,
            "undated": disposition_date is None,
        }
    )


def _action_domain(action: str) -> str | None:
    for domain, actions in ACTION_VOCAB_BY_DOMAIN.items():
        if action in actions:
            return domain
    return None


def _hub_primary_domain(entity_id: str) -> str:
    lower = entity_id.lower()
    if "boe19p" in lower or ("appeal" in lower and "escrow" not in lower):
        return "tax_appeal"
    return "mortgage_escrow"


def _proximity_rank(item: BurstClaimItem, *, hub_entity_id: str) -> tuple[int, int, int]:
    primary = _hub_primary_domain(hub_entity_id)
    action_domain = _action_domain(item.action)
    domain_rank = 0 if action_domain == primary else 1
    return (domain_rank, item.hop_distance or 0, item.assertion_id)


def _partition_terminal_rows(
    rows: list[BurstClaimItem],
    *,
    hub_entity_id: str,
) -> list[BurstClaimItem]:
    dated: list[tuple[BurstClaimItem, str]] = []
    undated: list[BurstClaimItem] = []
    for item in rows:
        disposition_date = _disposition_date(item)
        if disposition_date:
            dated.append((item, disposition_date))
        else:
            undated.append(item)
    dated.sort(
        key=lambda pair: (pair[1], *_proximity_rank(pair[0], hub_entity_id=hub_entity_id)),
        reverse=True,
    )
    undated.sort(key=lambda item: _proximity_rank(item, hub_entity_id=hub_entity_id))
    return [item for item, _ in dated] + undated


def resolve_terminal_facts(
    conn: sqlite3.Connection,
    entity_id: str,
) -> tuple[TerminalFactsBlock | None, str | None]:
    """Return terminal facts block and optional omission reason."""
    if not is_terminal_facts_hub(entity_id):
        return None, None

    radiation = radiate_terminal_facts_scope(conn, entity_id)
    scope_entity_ids = sorted(radiation.hop_distances)
    request = ClaimsBurstRequest(
        vocabulary=sorted(ACTION_VOCAB_V0),
        scope_entity_ids=scope_entity_ids,
        include_contradictions=False,
    )
    response = burst_claims(conn, request)

    primary_domain = _hub_primary_domain(entity_id)
    primary_vocab = ACTION_VOCAB_BY_DOMAIN.get(primary_domain, frozenset())

    by_assertion: dict[int, BurstClaimItem] = {}
    for item in response.claims:
        if not item.terminal:
            continue
        if item.action not in primary_vocab:
            continue
        hop = radiation.hop_distances.get(item.entity_id, 0)
        path = radiation.arrival_paths.get(item.entity_id, [entity_id])
        enriched = item.model_copy(
            update={"hop_distance": hop, "arrival_path": path},
        )
        existing = by_assertion.get(item.assertion_id)
        if existing is None or (enriched.hop_distance or 0) < (existing.hop_distance or 0):
            by_assertion[item.assertion_id] = enriched

    if not by_assertion:
        return None, None

    ordered = _partition_terminal_rows(list(by_assertion.values()), hub_entity_id=entity_id)
    facts = [_compact_terminal_fact(item) for item in ordered]
    fact_count = len(facts)
    capped = fact_count > TERMINAL_FACTS_CAP
    facts_dropped = max(0, fact_count - TERMINAL_FACTS_CAP)

    return (
        TerminalFactsBlock(
            facts=facts[:TERMINAL_FACTS_CAP],
            cap=TERMINAL_FACTS_CAP,
            capped=capped,
            fact_count=fact_count,
            facts_dropped=facts_dropped,
            scope_truncated=radiation.truncated,
            scope_size=len(scope_entity_ids),
            scope_cap=scope_radiation.HUB_SCOPE_ENTITY_CAP,
            detector_version=DETECTOR_VERSION,
        ),
        None,
    )


def attach_terminal_facts(
    conn: sqlite3.Connection,
    payload: dict[str, object],
    *,
    entity_id: str,
) -> None:
    """Mutate an entity_get payload with terminal_facts when applicable."""
    if not is_terminal_facts_hub(entity_id):
        return
    try:
        block, omitted_reason = resolve_terminal_facts(conn, entity_id)
    except Exception as exc:
        payload["terminal_facts_omitted_reason"] = f"terminal_facts_unavailable: {exc}"
        return
    if omitted_reason:
        payload["terminal_facts_omitted_reason"] = omitted_reason
        return
    if block is None:
        return
    payload["terminal_facts"] = block.model_dump(mode="json")
