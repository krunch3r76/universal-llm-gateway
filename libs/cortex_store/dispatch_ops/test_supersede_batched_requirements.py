"""`supersede` reports all its unmet requirements in one response.

Before this fix the op returned on the first unmet check, so a caller who
omitted both `old_assertion_id` and `evidence` learned about `old_assertion_id`,
supplied it, then learned about `evidence` — two round trips for one call's
requirements.
"""

from __future__ import annotations

import pytest

from cortex_store.dispatch_ops.ops_assertions_update import _op_supersede

pytestmark = pytest.mark.offline


def _errors(result: dict) -> list[dict]:
    err = result.get("error")
    assert isinstance(err, dict), f"expected a structured error, got {err!r}"
    return err.get("errors", [])


def test_both_missing_fields_land_in_one_response() -> None:
    result = _op_supersede()
    fields = {entry["field"] for entry in _errors(result)}
    assert fields == {
        "old_assertion_id",
        "entity_id",
        "claim",
        "confidence",
        "evidence",
    }


def test_partial_omission_batches_with_remaining_fields() -> None:
    result = _op_supersede(old_assertion_id=1, entity_id="todo:probe")
    fields = {entry["field"] for entry in _errors(result)}
    assert fields == {"claim", "confidence", "evidence"}


def test_single_omission_keeps_the_flat_shape() -> None:
    """One error still reports as a flat field error, not a list of one."""
    result = _op_supersede(
        old_assertion_id=1,
        entity_id="todo:probe",
        claim="updated claim",
        confidence="believed",
    )
    err = result["error"]
    assert err["field"] == "evidence"
    assert "errors" not in err
