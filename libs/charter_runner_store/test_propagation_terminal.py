"""Unit tests for propagation ledger terminal settlement from observed proof."""

from __future__ import annotations

import time

import pytest

from charter_runner_store.propagation_ledger import (
    OpenPropagationProjection,
    close_row,
    list_open_rows,
    upsert_open_rows,
)
from charter_runner_store.propagation_terminal import (
    _probe_is_outgoing_generation,
    reconcile_all_open_rows,
    settle_open_row,
    settle_open_rows_for_service,
)
from deploy_identity.code_version import reset_code_version_cache_for_tests
from implement_admission.propagation_row import PropagationRow


def _row(**kwargs: object) -> PropagationRow:
    base = {
        "service": "git_integration_worker",
        "code_ref": "abc123sha00000000000000000000000000000000",
    }
    base.update(kwargs)
    return PropagationRow(**base)


def test_queued_row_closes_on_matching_probe(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CHARTER_RUNNER_DATA_DIR", str(tmp_path))
    sha = "abc123sha00000000000000000000000000000000"
    upsert_open_rows([_row(code_ref=sha)])
    open_before = list_open_rows()
    assert len(open_before) == 1

    def probe(_service: str) -> dict[str, str]:
        return {"code_version": sha}

    result = settle_open_row(open_before[0], probe)
    assert result.outcome == "closed"
    assert list_open_rows() == []


def test_queued_row_fails_on_mismatched_probe(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CHARTER_RUNNER_DATA_DIR", str(tmp_path))
    expected = "abc123sha00000000000000000000000000000000"
    upsert_open_rows([_row(code_ref=expected)])
    row = list_open_rows()[0]

    def probe(_service: str) -> dict[str, str]:
        return {"code_version": "other0000000000000000000000000000000000"}

    result = settle_open_row(row, probe)
    assert result.outcome == "failed"
    assert "mismatch" in result.detail
    assert list_open_rows() == []


def test_settle_is_idempotent_on_second_pass(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CHARTER_RUNNER_DATA_DIR", str(tmp_path))
    sha = "deadbeef00000000000000000000000000000000"
    row_id = upsert_open_rows([_row(code_ref=sha)])[0]

    def probe(_service: str) -> dict[str, str]:
        return {"code_version": sha}

    first = settle_open_rows_for_service("git_integration_worker", probe)
    assert len(first) == 1
    assert first[0].outcome == "closed"
    second = settle_open_rows_for_service("git_integration_worker", probe)
    assert second == []
    assert list_open_rows() == []


def test_head_resolves_at_mint(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CHARTER_RUNNER_DATA_DIR", str(tmp_path))
    resolved = "resolved0000000000000000000000000000000000"
    monkeypatch.setenv("ULG_CODE_VERSION", resolved)
    reset_code_version_cache_for_tests()
    row_id = upsert_open_rows([_row(code_ref="HEAD")])[0]
    assert row_id == f"git_integration_worker:{resolved}:sync_restart"
    open_row = list_open_rows()[0]
    assert open_row.code_ref == resolved


def test_literal_head_row_unsettled_at_reconcile(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CHARTER_RUNNER_DATA_DIR", str(tmp_path))
    db = tmp_path / "root-ledger.sqlite"
    from charter_runner_store.db import open_ledger_db

    conn = open_ledger_db(db)
    conn.execute(
        """
        INSERT INTO propagation_ledger (
          row_id, service, action, code_ref, safe_window, proof, proof_class,
          status, age_in_harvests, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, 'open', 0, 1.0, 1.0)
        """,
        (
            "git_integration_worker:HEAD:sync_restart",
            "git_integration_worker",
            "sync_restart",
            "HEAD",
            "drain_required",
            "probe",
            "process_live",
        ),
    )
    conn.commit()
    conn.close()

    report = reconcile_all_open_rows(lambda _s: {"code_version": "anything"})
    assert report["before_open"] == 1
    assert report["after_open"] == 1
    assert report["unsettled"] == 1
    assert "HEAD" in report["results"][0].detail


def test_reconcile_before_after_counts(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CHARTER_RUNNER_DATA_DIR", str(tmp_path))
    sha_a = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    sha_b = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    upsert_open_rows(
        [
            _row(service="mcp", code_ref=sha_a),
            _row(service="mcp", code_ref=sha_b),
        ]
    )

    def probe(service: str) -> dict[str, str] | None:
        if service == "mcp":
            return {"code_version": sha_a}
        return None

    report = reconcile_all_open_rows(probe)
    assert report["before_open"] == 2
    assert report["after_open"] == 0
    assert report["closed"] == 1
    assert report["failed"] == 1


def test_outgoing_generation_probe_defers_on_mismatch(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CHARTER_RUNNER_DATA_DIR", str(tmp_path))
    expected = "abc123sha00000000000000000000000000000000"
    upsert_open_rows([_row(code_ref=expected)])
    row = list_open_rows()[0]
    settle_not_before = time.monotonic()

    def probe(_service: str) -> dict[str, float | str]:
        return {
            "code_version": "other0000000000000000000000000000000000",
            "uptime_s": 600.0,
        }

    result = settle_open_row(
        row,
        probe,
        defer_if_unreachable=True,
        settle_not_before_monotonic=settle_not_before,
    )
    assert result.outcome == "deferred"
    assert "outgoing generation" in result.detail
    assert len(list_open_rows()) == 1


def test_genuine_post_restart_mismatch_fails(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CHARTER_RUNNER_DATA_DIR", str(tmp_path))
    expected = "abc123sha00000000000000000000000000000000"
    upsert_open_rows([_row(code_ref=expected)])
    row = list_open_rows()[0]
    settle_not_before = time.monotonic() - 30.0

    def probe(_service: str) -> dict[str, float | str]:
        return {
            "code_version": "other0000000000000000000000000000000000",
            "uptime_s": 2.0,
        }

    result = settle_open_row(
        row,
        probe,
        defer_if_unreachable=True,
        settle_not_before_monotonic=settle_not_before,
    )
    assert result.outcome == "failed"
    assert "mismatch" in result.detail
    assert list_open_rows() == []


def test_matching_post_restart_probe_closes(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CHARTER_RUNNER_DATA_DIR", str(tmp_path))
    sha = "abc123sha00000000000000000000000000000000"
    upsert_open_rows([_row(code_ref=sha)])
    row = list_open_rows()[0]
    settle_not_before = time.monotonic() - 30.0

    def probe(_service: str) -> dict[str, float | str]:
        return {"code_version": sha, "uptime_s": 2.0}

    result = settle_open_row(
        row,
        probe,
        defer_if_unreachable=True,
        settle_not_before_monotonic=settle_not_before,
    )
    assert result.outcome == "closed"
    assert list_open_rows() == []


def test_probe_is_outgoing_generation_guard_condition() -> None:
    settle_not_before = time.monotonic()
    assert _probe_is_outgoing_generation(
        {"uptime_s": 600.0}, settle_not_before_monotonic=settle_not_before
    )
    assert not _probe_is_outgoing_generation(
        {"uptime_s": 2.0}, settle_not_before_monotonic=settle_not_before - 30.0
    )
    assert not _probe_is_outgoing_generation(
        {"code_version": "sha"}, settle_not_before_monotonic=settle_not_before
    )
