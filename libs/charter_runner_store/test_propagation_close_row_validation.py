"""Tests for transactional close_row validation pairing."""

from __future__ import annotations

import json
import sqlite3
import time

from charter_runner_store.propagation_ledger import close_row, upsert_open_rows
from implement_admission.propagation_row import PropagationRow


def _open_row(tmp_path, monkeypatch) -> str:
    monkeypatch.setenv("CHARTER_RUNNER_DATA_DIR", str(tmp_path))
    row = PropagationRow(
        service="git_integration_worker",
        code_ref="a" * 40,
        action="sync_restart",
        safe_window="harvest",
        proof_class="process_live",
    )
    upsert_open_rows([row])
    from charter_runner_store.propagation_ledger import list_open_rows

    return list_open_rows()[0].row_id


def test_preclosed_row_creates_no_validation(tmp_path, monkeypatch) -> None:
    row_id = _open_row(tmp_path, monkeypatch)
    from charter_runner_store import propagation_validation
    from charter_runner_store.db import open_ledger_db

    db = open_ledger_db()
    db.execute(
        "UPDATE propagation_ledger SET status='closed' WHERE row_id=?", (row_id,)
    )
    db.commit()
    before = len(propagation_validation.pending_validations())
    close_row(row_id, proof_payload={"code_version": "a" * 40, "proof_before": {}})
    after = len(propagation_validation.pending_validations())
    assert before == after
