"""Recall card assembly — conn-only orchestration for G1 life-recall."""

from __future__ import annotations

import sqlite3
from typing import Any, Literal

from .activation import spreading_activation
from .db import query
from .models.terminal_facts import TerminalFactsOmissionReason
from .recall_models import (
    AssociationRow,
    DispositionRow,
    RecallCard,
    RecallDisclosure,
    RecallNextAdvisory,
    RecallNull,
    ResolvedEntity,
)
from .recall_resolve import resolve_recall_inputs
from .routes.boot.continuity import get_boot_continuity
from .routes.boot.todos import get_boot_todos
from .scope_radiation import radiate_scope
from .terminal_facts import is_terminal_facts_hub, resolve_terminal_facts

_BOOT_AGENT = "cursor"
_OPEN_DEADLINES_SQL = """
    SELECT d.id AS deadline_id, d.name AS deadline_name,
           COALESCE(
               json_extract(d.attributes, '$.deadline_date'),
               json_extract(d.attributes, '$.date')
           ) AS deadline_date
    FROM entities d
    WHERE d.type = 'deadline'
      AND (json_extract(d.attributes, '$.outcome') IS NULL
           OR json_extract(d.attributes, '$.outcome') NOT IN
              ('defaulted', 'met', 'withdrawn', 'superseded'))
      AND COALESCE(
              json_extract(d.attributes, '$.deadline_date'),
              json_extract(d.attributes, '$.date')
          ) >= date('now')
    ORDER BY deadline_date ASC
    LIMIT 10
"""


def build_recall_card(
    conn: sqlite3.Connection,
    *,
    mode: Literal["matter", "continuity"],
    q: str | None,
    seeds: list[str] | None,
) -> RecallCard:
    """Compose resolve → radiate → burst → activate into one recall card."""
    if mode == "continuity":
        return _build_continuity_card(conn, q=q, seeds=seeds)
    return _build_matter_card(conn, q=q, seeds=seeds)


def _disposition_rows(block_facts: list[Any]) -> list[DispositionRow]:
    rows: list[DispositionRow] = []
    for fact in block_facts:
        rows.append(
            DispositionRow(
                predicate_form=str(fact.predicate_form),
                party=str(fact.party),
                disposition_date=fact.disposition_date,
                assertion_id=int(fact.assertion_id),
                epistemic_state=fact.epistemic_state,
                machine_derived=bool(fact.machine_derived),
                hop_distance=fact.hop_distance,
                arrival_path=fact.arrival_path,
            )
        )
    return rows


def _association_rows(result: Any) -> list[AssociationRow]:
    return [
        AssociationRow(
            claim=str(item.claim),
            assertion_id=int(item.assertion_id),
            activation_path=item.activation_path,
            entrenchment=item.entrenchment_score,
        )
        for item in result.activated
    ]


_THIN_ASSOCIATION_FLOOR = 3


def _next_advisory(
    nulls: list[RecallNull],
    *,
    candidate_count: int = 0,
    association_count: int = 0,
    disposition_count: int = 0,
    resolved: bool = False,
) -> RecallNextAdvisory | None:
    """Advisory escalate — reason only; seat maps to repair-or-recon."""
    if candidate_count > 0:
        return RecallNextAdvisory(reason="pin_seed")
    if RecallNull.resolver_miss in nulls:
        return RecallNextAdvisory(reason="resolver_miss")
    if RecallNull.vocab_not_covered in nulls:
        return RecallNextAdvisory(reason="vocab_not_covered")
    if RecallNull.scope_truncated in nulls:
        return RecallNextAdvisory(reason="scope_truncated")
    if RecallNull.nothing_on_record in nulls:
        return RecallNextAdvisory(reason="nothing_on_record")
    if (
        resolved
        and disposition_count == 0
        and association_count < _THIN_ASSOCIATION_FLOOR
    ):
        return RecallNextAdvisory(reason="thin_card")
    return None


def _open_deadlines(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = query(conn, _OPEN_DEADLINES_SQL, ())
    return [
        {
            "deadline_id": row["deadline_id"],
            "deadline_name": row["deadline_name"],
            "deadline_date": row["deadline_date"],
        }
        for row in rows
    ]


def _continuity_block(conn: sqlite3.Connection) -> dict[str, Any]:
    boot_continuity = get_boot_continuity(agent=_BOOT_AGENT, omit_resolved=True)
    boot_todos = get_boot_todos(
        limit=15,
        context=None,
        domain_exclude=None,
        compact=True,
    )
    return {
        "last_session": boot_continuity.get("last_session"),
        "open_todos": boot_todos.get("items", []),
        "open_deadlines": _open_deadlines(conn),
    }


def _continuity_seed_ids(
    continuity: dict[str, Any],
    resolved: list[ResolvedEntity],
) -> list[str]:
    ids: list[str] = []
    seen: set[str] = set()
    for item in resolved:
        if item.entity_id not in seen:
            seen.add(item.entity_id)
            ids.append(item.entity_id)
    for todo in continuity.get("open_todos") or []:
        if isinstance(todo, dict):
            todo_id = todo.get("id")
            if todo_id and todo_id not in seen:
                seen.add(str(todo_id))
                ids.append(str(todo_id))
    last_session = continuity.get("last_session")
    if isinstance(last_session, dict):
        transcript_id = last_session.get("transcript_entity_id")
        if transcript_id and transcript_id not in seen:
            seen.add(str(transcript_id))
            ids.append(str(transcript_id))
    return ids


def _build_continuity_card(
    conn: sqlite3.Connection,
    *,
    q: str | None,
    seeds: list[str] | None,
) -> RecallCard:
    outcome = resolve_recall_inputs(conn, q=q, seeds=seeds)
    continuity = _continuity_block(conn)
    has_continuity = bool(
        continuity.get("last_session")
        or continuity.get("open_todos")
        or continuity.get("open_deadlines")
    )

    nulls: list[RecallNull] = []
    if outcome.resolver_miss and not has_continuity and not outcome.resolved:
        nulls.append(RecallNull.resolver_miss)

    seed_ids = _continuity_seed_ids(continuity, outcome.resolved)
    associations: list[AssociationRow] = []
    if seed_ids:
        activation = spreading_activation(conn, seed_ids, depth=1, max_results=20)
        associations = _association_rows(activation)

    if (
        not nulls
        and not associations
        and not has_continuity
        and not outcome.resolved
    ):
        nulls.append(RecallNull.nothing_on_record)

    return RecallCard(
        mode="continuity",
        resolved=outcome.resolved,
        candidates=outcome.candidates,
        dispositions=[],
        associations=associations,
        continuity=continuity,
        disclosure=RecallDisclosure(vocabulary_covered=False),
        nulls=nulls,
        next_advisory=_next_advisory(nulls),
    )


def _build_matter_card(
    conn: sqlite3.Connection,
    *,
    q: str | None,
    seeds: list[str] | None,
) -> RecallCard:
    outcome = resolve_recall_inputs(conn, q=q, seeds=seeds)
    nulls: list[RecallNull] = []
    disclosure = RecallDisclosure()
    dispositions: list[DispositionRow] = []
    associations: list[AssociationRow] = []

    if outcome.candidates:
        return RecallCard(
            mode="matter",
            candidates=outcome.candidates,
            disclosure=disclosure,
            nulls=nulls,
            next_advisory=_next_advisory(
                nulls,
                candidate_count=len(outcome.candidates),
            ),
        )

    if outcome.resolver_miss:
        nulls.append(RecallNull.resolver_miss)
        return RecallCard(
            mode="matter",
            disclosure=disclosure,
            nulls=nulls,
            next_advisory=_next_advisory(nulls),
        )

    resolved_ids = [item.entity_id for item in outcome.resolved]
    scope_truncated = False
    vocab_not_covered = False
    hub_seen = False

    for hub_id in resolved_ids:
        if is_terminal_facts_hub(hub_id):
            hub_seen = True
            scope = radiate_scope(conn, hub_id)
            if scope.truncated:
                scope_truncated = True
            block, omitted = resolve_terminal_facts(conn, hub_id)
            if omitted in (
                TerminalFactsOmissionReason.hub_domain_unrecognized.value,
                TerminalFactsOmissionReason.terminal_claims_outside_primary_vocabulary.value,
            ):
                vocab_not_covered = True
            elif block is not None:
                disclosure.vocabulary_covered = True
                dispositions.extend(_disposition_rows(block.facts))
                if block.scope_truncated:
                    scope_truncated = True
                disclosure.rows_returned += block.fact_count
                disclosure.caps_hit = disclosure.caps_hit or block.capped
        else:
            scope = radiate_scope(conn, hub_id)
            if scope.truncated:
                scope_truncated = True

    if resolved_ids and not hub_seen:
        vocab_not_covered = True

    if scope_truncated:
        nulls.append(RecallNull.scope_truncated)
        disclosure.scope_truncated = True
    if vocab_not_covered:
        nulls.append(RecallNull.vocab_not_covered)

    if resolved_ids:
        activation = spreading_activation(conn, resolved_ids, depth=1, max_results=20)
        associations = _association_rows(activation)
        disclosure.rows_returned += len(associations)

    if (
        outcome.resolved
        and not dispositions
        and not associations
    ):
        nulls.append(RecallNull.nothing_on_record)

    return RecallCard(
        mode="matter",
        resolved=outcome.resolved,
        dispositions=dispositions,
        associations=associations,
        continuity=None,
        disclosure=disclosure,
        nulls=nulls,
        next_advisory=_next_advisory(
            nulls,
            association_count=len(associations),
            disposition_count=len(dispositions),
            resolved=bool(outcome.resolved),
        ),
    )
