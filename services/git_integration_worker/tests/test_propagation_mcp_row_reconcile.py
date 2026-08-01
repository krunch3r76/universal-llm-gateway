"""Tests for mcp row proof_class reconciliation (arc 6637 AC1, arc 6627 AC4)."""

from __future__ import annotations

from charter_runner_store.propagation_ledger import (
    list_open_rows,
    upsert_open_rows,
)
from charter_runner_store.propagation_terminal import settle_open_row
from implement_admission.propagation_row import PropagationRow
from services.git_integration_worker.cursor_auto.propagation_proof_reconcile import (
    reconcile_unsupported_proof_class,
)


_SHA = "d3e17d54b66276a350769501beb90c2988ff3bf1"


def test_reconcile_mcp_served_artifact_fails_loud_not_downgrade(
    tmp_path, monkeypatch,
) -> None:
    monkeypatch.setenv("CHARTER_RUNNER_DATA_DIR", str(tmp_path))
    upsert_open_rows(
        [
            PropagationRow(
                service="mcp",
                code_ref=_SHA,
                safe_window="standalone_ok",
                proof="test",
                proof_class="served_artifact",
            )
        ]
    )
    row = list_open_rows()[0]
    assert row.proof_class == "served_artifact"

    detail = reconcile_unsupported_proof_class(row)
    assert detail is not None
    assert detail.startswith("proof_class_unsupported:")
    assert "service=mcp" in detail
    assert "requested=served_artifact" in detail
    assert list_open_rows() == []


def test_settle_unsupported_proof_class_fails_loud(tmp_path, monkeypatch) -> None:
    """SETTLE path must fail unsupported proof_class — not downgrade and probe."""
    monkeypatch.setenv("CHARTER_RUNNER_DATA_DIR", str(tmp_path))
    upsert_open_rows(
        [
            PropagationRow(
                service="mcp",
                code_ref=_SHA,
                safe_window="standalone_ok",
                proof="test",
                proof_class="served_artifact",
            )
        ]
    )
    row = list_open_rows()[0]

    result = settle_open_row(row, lambda _service: {"code_version": _SHA})
    assert result.outcome == "failed"
    assert result.detail.startswith("proof_class_unsupported:")
    assert list_open_rows() == []


def test_mcp_client_visible_blocked_by_cortex_api_skew_stays_open(
    tmp_path, monkeypatch,
) -> None:
    monkeypatch.setenv("CHARTER_RUNNER_DATA_DIR", str(tmp_path))
    upsert_open_rows(
        [
            PropagationRow(
                service="mcp",
                code_ref=_SHA,
                safe_window="standalone_ok",
                proof="test",
                proof_class="client_visible",
            )
        ]
    )
    row = list_open_rows()[0]
    payload = {
        "mcp_health": {
            "code_version": _SHA,
        },
        "cortex_api": {
            "code_version": "a50a554c7b315633642ccadbc7366db74d026506",
        },
    }

    result = settle_open_row(row, lambda _service: payload, defer_if_unreachable=True)
    assert result.outcome == "deferred"
    assert list_open_rows()
    assert "contradiction" in result.detail.lower() or "observed" in result.detail.lower()
