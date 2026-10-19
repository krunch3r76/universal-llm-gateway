"""K43 finding 2 — non-terminal conductor rows surface on ``/active-work``."""

from __future__ import annotations

import json

import pytest

from services.git_integration_worker.cursor_dispatch_ledger import (
    CursorDispatchLedger,
    _connect,
)

_REPO_A = "/repo/hub-master"
_LANE_B_LEASE = "/wt/universal-llm-gateway/lane-18290"
_NON_TERMINAL = ("admitted", "queued", "claimed", "running", "parked_waiting")


@pytest.fixture(autouse=True)
def _isolated_ledger(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    CursorDispatchLedger._instance = None
    yield
    CursorDispatchLedger._instance = None


def _insert_conductor(
    *,
    dispatch_id: str,
    status: str,
    work_key: str,
    source_ref: str,
    lease_key: str,
    read_only: int = 0,
) -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT INTO cursor_sdk_dispatches "
            "(dispatch_id, fingerprint, thread_id, execution_id, resolved_model, "
            " message_present, status, record_json, source_repo, lease_key, "
            " read_only, work_key, source_ref, contract) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                dispatch_id,
                f"fp-{dispatch_id}",
                f"thread-{dispatch_id}",
                f"exec-{dispatch_id}",
                "composer-2.5",
                0,
                status,
                json.dumps({"lane": "B" if lease_key != _REPO_A else "A"}),
                _REPO_A,
                lease_key,
                read_only,
                work_key,
                source_ref,
                "conductor",
            ),
        )


def test_active_work_lists_every_non_terminal_conductor_across_lanes() -> None:
    ledger = CursorDispatchLedger.instance()
    for lane, lease_key in (("a", _REPO_A), ("b", _LANE_B_LEASE)):
        for status in _NON_TERMINAL:
            slug = f"k43-{lane}-{status.replace('_', '-')}"
            work_key = f"todo:{slug}"
            source_ref = f"agent-bus:18290#{slug}"
            _insert_conductor(
                dispatch_id=f"disp-{lane}-{status}",
                status=status,
                work_key=work_key,
                source_ref=source_ref,
                lease_key=lease_key,
                read_only=0,
            )

    snap = ledger.lease_snapshot(source_repo=_REPO_A)
    holders = snap["active_holders"]
    store_count = snap["non_terminal_conductor_count"]
    assert store_count == len(_NON_TERMINAL) * 2
    assert len(holders) == store_count
    keyed = {str(h["work_key"]).lower() for h in holders}
    assert len(keyed) == store_count
    for row in holders:
        assert row.get("contract") == "conductor"
        assert str(row.get("work_key") or "").startswith("todo:k43-")
        assert row.get("source_ref")
