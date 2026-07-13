"""Life intent lifecycle events — life.intent.* namespace."""

from __future__ import annotations

from universal_event_bus import Event, event_factory


@event_factory
def life_intent_received(
    *,
    verb: str,
    ref_count: int,
    context: str,
) -> Event:
    """life.intent.received — intent received and passed registry envelope check."""
    return Event(
        signal="life.intent.received",
        role="observation",
        scope="global",
        payload={
            "verb": verb,
            "ref_count": ref_count,
            "context": context,
        },
    )


@event_factory
def life_intent_proposed(
    *,
    verb: str,
    question_count: int,
    proposal_id: str,
) -> Event:
    """life.intent.proposed — work order produced for a valid intent."""
    return Event(
        signal="life.intent.proposed",
        role="observation",
        scope="global",
        payload={
            "verb": verb,
            "question_count": question_count,
            "proposal_id": proposal_id,
        },
    )


@event_factory
def life_intent_rejected(
    *,
    verb: str | None,
    reject_count: int,
    reject_codes: list[str],
) -> Event:
    """life.intent.rejected — refuse-list / vocabulary / ref reject."""
    return Event(
        signal="life.intent.rejected",
        role="observation",
        scope="global",
        payload={
            "verb": verb,
            "reject_count": reject_count,
            "reject_codes": reject_codes,
        },
    )


@event_factory
def life_intent_committed(
    *,
    verb: str,
    proposal_id: str,
    entity_id: str | None,
    dispatch_ref: str,
) -> Event:
    """life.intent.committed — commit applied and downstream scout fired."""
    return Event(
        signal="life.intent.committed",
        role="observation",
        scope="global",
        payload={
            "verb": verb,
            "proposal_id": proposal_id,
            "entity_id": entity_id,
            "dispatch_ref": dispatch_ref,
        },
    )
