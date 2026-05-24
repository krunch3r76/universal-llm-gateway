"""v1.3.1 write-once: PATCH does not mutate ledger (enforced by AssertionUpdate lacking the fields)."""

from __future__ import annotations

from cortex_store.models.assertions import AssertionItem, AssertionUpdate


def test_assertion_update_lacks_ledger_fields() -> None:
    # Key invariant: model does not accept the 4 fields on PATCH
    fields = AssertionUpdate.model_fields.keys()
    assert "raw_predicate_form" not in fields
    assert "normalization_decision" not in fields
    # AssertionItem (read) does have them
    assert "raw_predicate_form" in AssertionItem.model_fields
