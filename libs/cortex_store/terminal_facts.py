"""Terminal facts enrich-on-read for case/account hub entity_get (arc 6386 slice 5a).

Read-only: derives terminal predicates from hub-scoped assertions via
``burst_claims`` — no seat-supplied vocabulary or draft access.
"""

from __future__ import annotations

import sqlite3

from predicate_form.action_vocabulary import ACTION_VOCAB_V0

from .claims_burst import burst_claims
from .models.claims_burst import ClaimsBurstRequest
from .models.terminal_facts import TerminalFactsBlock

TERMINAL_FACTS_CAP = 25
HUB_ID_PREFIXES = ("case:", "account:")


def is_terminal_facts_hub(entity_id: str) -> bool:
    """True when entity id uses a case or account hub prefix."""
    return entity_id.startswith(HUB_ID_PREFIXES)


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

    request = ClaimsBurstRequest(
        vocabulary=sorted(ACTION_VOCAB_V0),
        scope_entity_ids=[entity_id],
        include_contradictions=False,
    )
    response = burst_claims(conn, request)
    terminal_rows = [item for item in response.claims if item.terminal]
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
