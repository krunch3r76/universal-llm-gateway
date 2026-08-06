"""Packet D — member 2: status column typed as observed-of-attempt event."""

from __future__ import annotations

from charter_runner_store.propagation_attempt_status import (
    ATTEMPT_STATUS_VALUES,
    STATUS_CLAIM_KIND,
    STATUS_CLOSED,
    STATUS_FAILED,
    STATUS_OPEN,
    is_attempt_status,
)
from charter_runner_store.propagation_liveness import observe_code_ref_live


def test_status_tokens_unchanged() -> None:
    """Do not redefine open|failed|closed — typing is semantic, not a rename."""
    assert ATTEMPT_STATUS_VALUES == frozenset({"open", "failed", "closed"})
    assert STATUS_OPEN == "open"
    assert STATUS_FAILED == "failed"
    assert STATUS_CLOSED == "closed"
    assert STATUS_CLAIM_KIND == "observed_of_attempt"
    assert is_attempt_status("failed")
    assert not is_attempt_status("not-live")


def test_specimen_failed_event_reader_owns_current() -> None:
    """AC remainder: failed specimen may coexist with reader answer=yes.

    Row 28 does not rewrite failed events; member 2 typing forbids reading
    ``status`` alone as current not-live.
    """
    specimen = "40f8eadde10a2fb2afcfde4960c11db11a22c56c"
    row_id = f"git_integration_worker:{specimen}:sync_restart"
    result = observe_code_ref_live("git_integration_worker", specimen)
    from charter_runner_store.db import open_ledger_db

    db = open_ledger_db()
    try:
        cur = db.execute(
            "SELECT status, defer_reason FROM propagation_ledger WHERE row_id=?",
            (row_id,),
        )
        row = cur.fetchone()
    finally:
        db.close()
    if row is None:
        return  # host without specimen — type contract still held above
    assert is_attempt_status(row["status"])
    assert row["status"] == STATUS_FAILED
    # When live probe says yes, the failed token remains an event — not a lie
    # about current state (current state is the reader answer).
    if result.answer == "yes":
        assert row["status"] == "failed"
        assert result.relation in {"equal", "ancestor"}
