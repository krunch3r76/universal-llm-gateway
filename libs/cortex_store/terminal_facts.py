"""Terminal facts enrich-on-read for case/account hub entity_get (arc 6386 slice 5a).

Read-only: derives terminal predicates from hub-scoped assertions via
``burst_claims`` — no seat-supplied vocabulary or draft access.
"""

from __future__ import annotations

import sqlite3

from predicate_form.action_vocabulary import ACTION_VOCAB_V0

from .claims_burst import burst_claims
from .models.claims_burst import BurstClaimItem, ClaimsBurstRequest
from .models.terminal_facts import TerminalFactsBlock
from .subgraph_traversal import archived_set, bfs_traverse

TERMINAL_FACTS_CAP = 17
CLAIM_EXCERPT_MAX = 200
HUB_ID_PREFIXES = ("case:", "account:")
HUB_SCOPE_EDGE_TYPES = ["involves", "references", "related_to"]
FINANCE_PREFIX = "finance:"
HUB_SCOPE_ENTITY_CAP = 50


def is_terminal_facts_hub(entity_id: str) -> bool:
    """True when entity id uses a case or account hub prefix."""
    return entity_id.startswith(HUB_ID_PREFIXES)


def _hub_neighbors(
    conn: sqlite3.Connection,
    root: str,
    *,
    archived: set[str],
) -> dict[str, int]:
    return bfs_traverse(
        conn=conn,
        root=root,
        hops=1,
        edge_types=HUB_SCOPE_EDGE_TYPES,
        archived=archived,
        entity_cap=HUB_SCOPE_ENTITY_CAP,
    )


def resolve_terminal_facts_scope(
    conn: sqlite3.Connection,
    entity_id: str,
) -> list[str]:
    """Depth-1 hub scope: root plus linked case/account hubs via structural edges."""
    if not is_terminal_facts_hub(entity_id):
        return [entity_id]

    scope: set[str] = {entity_id}
    archived = archived_set(conn)
    visited = _hub_neighbors(conn, entity_id, archived=archived)

    for neighbor_id in visited:
        if neighbor_id == entity_id:
            continue
        if is_terminal_facts_hub(neighbor_id):
            scope.add(neighbor_id)
            continue
        if not neighbor_id.startswith(FINANCE_PREFIX):
            continue
        bridged = _hub_neighbors(conn, neighbor_id, archived=archived)
        for hub_id in bridged:
            if is_terminal_facts_hub(hub_id):
                scope.add(hub_id)

    return sorted(scope)


def _compact_terminal_fact(item: BurstClaimItem) -> BurstClaimItem:
    excerpt = item.claim_excerpt or item.claim
    if len(excerpt) > CLAIM_EXCERPT_MAX:
        excerpt = excerpt[: CLAIM_EXCERPT_MAX - 1].rstrip() + "…"
    return item.model_copy(
        update={
            "claim": excerpt,
            "claim_excerpt": excerpt,
            "derivation": item.derivation or "action_enrichment_template_v0",
        }
    )


def resolve_terminal_facts(
    conn: sqlite3.Connection,
    entity_id: str,
) -> tuple[TerminalFactsBlock | None, str | None]:
    """Return terminal facts block and optional omission reason.

    When no terminal rows exist, returns (None, None) — omit the block.
    On resolution failure, returns (None, reason) for the caller to surface.
    """
    if not is_terminal_facts_hub(entity_id):
        return None, None

    scope_entity_ids = resolve_terminal_facts_scope(conn, entity_id)
    request = ClaimsBurstRequest(
        vocabulary=sorted(ACTION_VOCAB_V0),
        scope_entity_ids=scope_entity_ids,
        include_contradictions=False,
    )
    response = burst_claims(conn, request)
    seen: set[int] = set()
    terminal_rows: list[BurstClaimItem] = []
    for item in response.claims:
        if not item.terminal or item.assertion_id in seen:
            continue
        seen.add(item.assertion_id)
        terminal_rows.append(_compact_terminal_fact(item))

    if not terminal_rows:
        return None, None

    capped = len(terminal_rows) > TERMINAL_FACTS_CAP
    facts = terminal_rows[:TERMINAL_FACTS_CAP]
    return (
        TerminalFactsBlock(
            facts=facts,
            cap=TERMINAL_FACTS_CAP,
            capped=capped,
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
