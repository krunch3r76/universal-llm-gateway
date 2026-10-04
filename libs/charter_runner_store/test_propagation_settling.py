"""Per-service settling exclusion on the propagation ledger."""

import time

from implement_admission.propagation_row import PropagationRow

from charter_runner_store.db import open_ledger_db
from charter_runner_store.propagation_ledger import (
    DEFER_OPERATOR_RETRACTED,
    mark_settling,
    provider_settle_verdicts,
    reclaim_stale_settling_rows,
    record_settle_verdict,
    release_settling,
    retract_row,
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
        assert release_settling(ids[0], conn=conn)
        assert not service_is_settling("stargate", conn=conn)
    finally:
        conn.close()


def test_reclaim_stale_settling_rows(tmp_path, monkeypatch):
    monkeypatch.setenv("CHARTER_RUNNER_DATA_DIR", str(tmp_path))
    row = PropagationRow(
        service="stargate",
        code_ref="HEAD",
        proof_class="functional_settle",
    )
    conn = open_ledger_db()
    try:
        ids = upsert_open_rows([row], conn=conn)
        assert mark_settling(ids[0], conn=conn)
        conn.execute(
            "UPDATE propagation_ledger SET updated_at=? WHERE row_id=?",
            (time.time() - 500.0, ids[0]),
        )
        conn.commit()
        assert reclaim_stale_settling_rows(stale_after_s=240.0, conn=conn) == 1
        assert not service_is_settling("stargate", conn=conn)
    finally:
        conn.close()


def test_retracted_row_verdict_excluded_from_provider_map(tmp_path, monkeypatch):
    monkeypatch.setenv("CHARTER_RUNNER_DATA_DIR", str(tmp_path))
    row = PropagationRow(service="stargate", code_ref="HEAD", proof_class="functional_settle")
    conn = open_ledger_db()
    try:
        ids = upsert_open_rows([row], conn=conn)
        record_settle_verdict(ids[0], "fail_attributable", conn=conn)
        assert provider_settle_verdicts(conn=conn)["stargate"] == "fail_attributable"
        assert retract_row(ids[0], reason="operator", authority="test", conn=conn)
        verdicts = provider_settle_verdicts(conn=conn)
        assert "stargate" not in verdicts
        assert f"stargate:{row.code_ref}" not in verdicts
    finally:
        conn.close()


def test_failed_non_retracted_verdict_still_blocks(tmp_path, monkeypatch):
    monkeypatch.setenv("CHARTER_RUNNER_DATA_DIR", str(tmp_path))
    row = PropagationRow(service="stargate", code_ref="HEAD", proof_class="functional_settle")
    conn = open_ledger_db()
    try:
        ids = upsert_open_rows([row], conn=conn)
        record_settle_verdict(ids[0], "fail_attributable", conn=conn)
        conn.execute(
            """
            UPDATE propagation_ledger
            SET status='failed', defer_reason='settle_fail_attributable'
            WHERE row_id=?
            """,
            (ids[0],),
        )
        conn.commit()
        assert provider_settle_verdicts(conn=conn)["stargate"] == "fail_attributable"
        assert DEFER_OPERATOR_RETRACTED != "settle_fail_attributable"
    finally:
        conn.close()
