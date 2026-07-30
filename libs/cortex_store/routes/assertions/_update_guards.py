"""Write guards for PATCH /assertions/{id} — temporal anchor and predicate class.

Both guards close hazards found while auditing the arc-6386 materialization
path (binder verdict at cortex://notes/system/threads/6386-materialize-verdict.md):

``valid_from`` is now patchable, but **fill-only** — it exists so a legacy row
missing a temporal anchor can be given one, not so an existing anchor can be
moved. ``predicate_form`` PATCH may not silently change a predicate's *class*,
because three consumers read that column as an asserted state predicate (the
card current-status slot, supersede candidacy by functor equality, and the
normalization ledger), and the create-time ledger of what the caller originally
seeded must survive a later writeback rather than being restated as though the
derived form had been seeded.

Both guards are escapable with ``force=true`` for known-intentional rewrites,
matching the existing ``superseded_by`` CAS escape in this route.
"""

from __future__ import annotations

from fastapi import HTTPException, status

from ...belief_guard import predicate_functor


def _blank(value: object) -> bool:
    return not (value and str(value).strip())


def guard_valid_from_fill_only(
    *,
    assertion_id: int,
    stored_valid_from: object,
    incoming_valid_from: str | None,
    force: bool,
) -> None:
    """Reject a valid_from PATCH that would move an existing temporal anchor."""
    if incoming_valid_from is None or force:
        return
    if _blank(stored_valid_from):
        return
    if str(stored_valid_from).strip() == incoming_valid_from.strip():
        return
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=(
            f"Assertion {assertion_id} already has valid_from "
            f"{str(stored_valid_from).strip()!r}; refusing to move a temporal "
            f"anchor to {incoming_valid_from.strip()!r}. valid_from PATCH is "
            f"fill-only — pass force=true to override."
        ),
    )


def guard_predicate_class_change(
    *,
    assertion_id: int,
    stored_predicate_form: object,
    incoming_predicate_form: str | None,
    force: bool,
) -> None:
    """Reject a predicate_form PATCH that changes the predicate's functor class."""
    if incoming_predicate_form is None or force:
        return
    stored_functor = predicate_functor(
        str(stored_predicate_form) if stored_predicate_form else None
    )
    if stored_functor is None:
        return
    incoming_functor = predicate_functor(incoming_predicate_form)
    if incoming_functor is None or incoming_functor == stored_functor:
        return
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=(
            f"Assertion {assertion_id} stores a {stored_functor!r} predicate; "
            f"refusing to replace it with a {incoming_functor!r} predicate. "
            f"Changing predicate class drops the row from consumers keyed on the "
            f"stored functor (card current-status slot, supersede candidacy). "
            f"Pass force=true to override."
        ),
    )


def preserve_seeded_ledger(
    update_map: dict[str, object],
    *,
    stored_raw_predicate_form: object,
) -> None:
    """Keep the create-time record of what the caller seeded.

    ``raw_predicate_form`` answers "what did the caller mean", so a later
    writeback must not restate it from the new normalize result — that would
    manufacture caller provenance for a form no caller ever supplied.
    """
    if _blank(stored_raw_predicate_form):
        return
    update_map.pop("raw_predicate_form", None)
