"""`friction` reports all its unmet requirements in one response.

Before this fix the op returned on the first unmet check, so a caller who
omitted both `owner` and `note` learned about `owner`, supplied it, then learned
about `note` — two round trips for one call's requirements. It also rejected
`claim`, the slot name used by the sibling assert/observe/supersede ops, so a
seat that supplied the note under the sibling name was told the note was
missing.
"""

from __future__ import annotations

import inspect

import pytest

from cortex_store.dispatch_ops.ops_assertions_friction import _op_friction

pytestmark = pytest.mark.offline


def _errors(result: dict) -> list[dict]:
    err = result.get("error")
    assert isinstance(err, dict), f"expected a structured error, got {err!r}"
    return err.get("errors", [])


def test_both_missing_fields_land_in_one_response() -> None:
    result = _op_friction()
    fields = {entry["field"] for entry in _errors(result)}
    assert fields == {"owner", "note"}


def test_invalid_category_batches_with_missing_fields() -> None:
    """An enum violation and two omissions are one round trip, not three."""
    result = _op_friction(category="not-a-category")
    entries = _errors(result)
    fields = {entry["field"] for entry in entries}
    assert fields == {"owner", "note", "category"}
    category = next(e for e in entries if e["field"] == "category")
    assert category["accepted"], "the caller must be told the accepted values"


def test_single_omission_keeps_the_flat_shape() -> None:
    """One error still reports as a flat field error, not a list of one."""
    result = _op_friction(owner="service:probe")
    err = result["error"]
    assert err["field"] == "note"
    assert "errors" not in err


def test_claim_is_accepted_as_an_alias_for_note() -> None:
    """`claim` is the slot name on assert/observe/supersede; accept it here.

    Probe owner does not exist, so the call still errors — but after field
    validation. Both structured (batched/flat) and plain-string 404 shapes are
    accepted; demanding ``note`` after ``claim`` was supplied is the failure.
    """
    assert "claim" in inspect.signature(_op_friction).parameters
    result = _op_friction(owner="service:probe", claim="something went wrong")
    err = result.get("error")
    assert err is not None, "expected an error for nonexistent probe owner"

    if isinstance(err, dict):
        fields = {e["field"] for e in err.get("errors", []) if isinstance(e, dict)}
        if err.get("field"):
            fields.add(err["field"])
        assert "note" not in fields, (
            "claim was supplied; note must not be demanded "
            f"(fields={fields})"
        )
        return

    assert isinstance(err, str), f"unexpected error shape: {type(err).__name__}: {err!r}"
    lowered = err.lower()
    assert "note is required" not in lowered, (
        "claim was supplied; note must not be demanded"
    )
    assert "not found" in lowered, (
        "expected entity-not-found after claim→note resolved; "
        f"got {err!r}"
    )


def test_owner_alias_named_in_the_message() -> None:
    """A caller who reached for the wrong name learns the right one in-band."""
    result = _op_friction()
    owner = next(e for e in _errors(result) if e["field"] == "owner")
    assert "service" in owner["message"]
    note = next(e for e in _errors(result) if e["field"] == "note")
    assert "claim" in note["message"]
