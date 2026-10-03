"""Per-service settling exclusion on the propagation ledger."""

from implement_admission.propagation_row import PropagationRow

from charter_runner_store.db import open_ledger_db
from charter_runner_store.propagation_ledger import (
    mark_settling,
    service_is_settling,
    upsert_open_rows,
)


def test_second_land_sees_settling_row(tmp_path, monkeypatch):
    monkeypatch.setenv("CHARTER_RUNNER_DATA_DIR", str(tmp_path))
    row = PropagationRow(
        service="stargate",
        code_ref="HEAD",
        proof_class="functional_settle",
        safe_window="harvest",
        proof="functional_settle obligation",
    )
    conn = open_ledger_db()
    try:
        ids = upsert_open_rows([row], conn=conn)
        assert mark_settling(ids[0], conn=conn)
        assert service_is_settling("stargate", conn=conn)
        assert not service_is_settling("mcp", conn=conn)
    finally:
        conn.close()
