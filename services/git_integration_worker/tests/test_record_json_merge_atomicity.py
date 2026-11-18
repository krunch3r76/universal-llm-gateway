"""Acceptance proofs: record_json merges hold the write lock across read/write."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from collections.abc import Callable
from typing import Any

import pytest

from services.git_integration_worker.cursor_dispatch_ledger import CursorDispatchLedger
from services.git_integration_worker.cursor_sdk_work_claim import update
from services.git_integration_worker.cursor_sdk_work_registry_admit import (
    build_cursor_sdk_registry_address,
    record_dispatch_registry_best_effort,
    stamp_registry_terminal_work_status,
)
from services.git_integration_worker.models.cursor_api import (
    CursorDispatchRequest,
    CursorDispatchResponse,
)

pytestmark = pytest.mark.offline

D = "disp-rjrace"
K = "todo:record-json-claim-race"
RACE_ADDRESS = {
    "kind": "cursor_sdk",
    "dispatch_id": D,
    "worker_thread": "16006",
    "worktree": "/tmp/lane-race",
}


@pytest.fixture
def ledger_and_path(tmp_path, monkeypatch):
    path = tmp_path / "ledger.sqlite"
    monkeypatch.setenv("CURSOR_SDK_DISPATCH_LEDGER", str(path))
    return CursorDispatchLedger(), path


def _admit_row(led: CursorDispatchLedger) -> None:
    req = CursorDispatchRequest(
        thread_id="16006",
        model="cursor/grok-4.7",
        dispatch_id=D,
        execution_id="exec-rjrace",
        message=None,
        packet_path="tmp/packet.md",
        read_only=True,
    )
    led.admit(
        req=req,
        fingerprint=led.fingerprint(req),
        execution_id=req.execution_id,
        caller_agent="seat-a",
        resolved_model="grok-4.7",
        admission=CursorDispatchResponse(
            admitted=True,
            dispatch_id=D,
            thread_id=req.thread_id,
            model_id="grok-4.7",
        ),
        contract="implement",
        read_only=True,
        work_key=K,
        source_ref=K,
        identity_class="declared",
    )


def _record(led: CursorDispatchLedger) -> dict[str, Any]:
    with led._connect() as conn:
        row = conn.execute(
            "SELECT record_json FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            (D,),
        ).fetchone()
    assert row is not None
    return json.loads(row["record_json"])


def _claim_update(led: CursorDispatchLedger, **extra: Any) -> dict[str, Any]:
    return update(
        D,
        "seat-a",
        note="race",
        address_patch=RACE_ADDRESS,
        caller_dispatch_id=D,
        ledger=led,
        **extra,
    )


def _write_lock_held(path) -> bool:
    probe = sqlite3.connect(str(path), timeout=0)
    try:
        try:
            probe.execute("BEGIN IMMEDIATE")
        except sqlite3.OperationalError as exc:
            if "locked" in str(exc).lower():
                return True
            raise
        probe.rollback()
        return False
    finally:
        probe.close()


class _Interleave:
    def __init__(self, path, w2: Callable[[], None]) -> None:
        self._path = path
        self._w2 = w2
        self.calls = 0
        self.errors: list[str] = []
        self.lock_held: bool | None = None
        self.w2_error: str | None = None
        self.thread: threading.Thread | None = None

    def fire(self) -> None:
        self.calls += 1
        try:
            self.lock_held = _write_lock_held(self._path)
            self.thread = threading.Thread(target=self._run_w2, daemon=True)
            self.thread.start()
            if self.lock_held is False:
                self.thread.join(timeout=10)
        except Exception as exc:  # noqa: BLE001 — driver must not escape into writer
            self.errors.append(repr(exc))

    def _run_w2(self) -> None:
        try:
            self._w2()
        except Exception as exc:  # noqa: BLE001
            self.w2_error = repr(exc)

    def finish(self) -> None:
        assert self.thread is not None
        self.thread.join(timeout=40)
        assert not self.thread.is_alive(), (
            f"lock_held={self.lock_held} errors={self.errors} w2_error={self.w2_error}"
        )
        assert self.calls == 1, (
            f"lock_held={self.lock_held} errors={self.errors} w2_error={self.w2_error}"
        )
        assert self.errors == [], (
            f"lock_held={self.lock_held} errors={self.errors} w2_error={self.w2_error}"
        )
        assert self.w2_error is None, (
            f"lock_held={self.lock_held} errors={self.errors} w2_error={self.w2_error}"
        )


class _HookedPatch:
    def __init__(self, data: dict[str, Any], on_first_read: Callable[[], None]) -> None:
        self._data = dict(data)
        self._on_first_read = on_first_read
        self._fired = False

    def _maybe_fire(self) -> None:
        if not self._fired:
            self._fired = True
            self._on_first_read()

    def keys(self):
        self._maybe_fire()
        return self._data.keys()

    def items(self):
        self._maybe_fire()
        return self._data.items()

    def __getitem__(self, key: str) -> Any:
        return self._data[key]


class _HookedSeat(str):
    def __new__(cls, value: str, on_first_str: Callable[[], None]):
        obj = str.__new__(cls, value)
        obj._on_first_str = on_first_str  # type: ignore[attr-defined]
        obj._fired = False  # type: ignore[attr-defined]
        return obj

    def __str__(self) -> str:
        if not self._fired:  # type: ignore[attr-defined]
            self._fired = True  # type: ignore[attr-defined]
            self._on_first_str()  # type: ignore[attr-defined]
        return str.__str__(self)


def test_merge_record_json_keeps_overlapping_claim_update(ledger_and_path) -> None:
    led, path = ledger_and_path
    _admit_row(led)
    drv = _Interleave(path, lambda: _claim_update(led, work_status="active"))
    p = _HookedPatch({"sdk_mode": "agent"}, drv.fire)
    led.merge_record_json(dispatch_id=D, patch=p)  # type: ignore[arg-type]
    drv.finish()
    rec = _record(led)
    assert rec["sdk_mode"] == "agent"
    history = rec.get("status_history") or []
    assert any(h.get("note") == "race" for h in history)
    assert rec.get("work_status") == "active"
    assert (rec.get("address") or {}).get("worktree") == "/tmp/lane-race"
    assert rec["model"] == "cursor/grok-4.7"
    assert rec["packet_path"] == "tmp/packet.md"


def test_registry_stamp_keeps_overlapping_claim_update(ledger_and_path) -> None:
    led, path = ledger_and_path
    _admit_row(led)
    drv = _Interleave(path, lambda: _claim_update(led))
    seat = _HookedSeat("seat-a", drv.fire)
    out = record_dispatch_registry_best_effort(
        dispatch_id=D,
        thread_id="16006",
        caller_agent="seat-a",
        seat_name=seat,
        address=build_cursor_sdk_registry_address(
            dispatch_id=D,
            worker_thread="16006",
            worktree="/tmp/lane-stamp",
        ),
        work_key=K,
        ledger=led,
    )
    assert out == D
    drv.finish()
    rec = _record(led)
    history = rec.get("status_history") or []
    assert any(h.get("note") == "race" for h in history)
    assert rec["address"]["worktree"] == "/tmp/lane-race"
    assert rec.get("registry_auto_recorded") is True
    assert rec.get("seat_name") == "seat-a"
    assert rec.get("work_status") == "active"
    assert rec["model"] == "cursor/grok-4.7"
    assert rec["packet_path"] == "tmp/packet.md"


def test_merge_record_json_subobject_keeps_overlapping_claim_update(
    ledger_and_path,
) -> None:
    led, path = ledger_and_path
    _admit_row(led)
    drv = _Interleave(path, lambda: _claim_update(led, work_status="active"))
    p = _HookedPatch({"stream_tool_call_count": 3}, drv.fire)
    led.merge_record_json_subobject(
        dispatch_id=D,
        subkey="density_harness_control",
        patch=p,  # type: ignore[arg-type]
    )
    drv.finish()
    rec = _record(led)
    assert rec["density_harness_control"] == {"stream_tool_call_count": 3}
    history = rec.get("status_history") or []
    assert any(h.get("note") == "race" for h in history)
    assert rec.get("work_status") == "active"
    assert (rec.get("address") or {}).get("worktree") == "/tmp/lane-race"
    assert rec["model"] == "cursor/grok-4.7"
    assert rec["packet_path"] == "tmp/packet.md"


def test_terminal_work_status_stamp_keeps_overlapping_write(ledger_and_path) -> None:
    led, path = ledger_and_path
    _admit_row(led)
    assert (
        record_dispatch_registry_best_effort(
            dispatch_id=D,
            thread_id="16006",
            caller_agent="seat-a",
            seat_name="seat-a",
            address=build_cursor_sdk_registry_address(
                dispatch_id=D,
                worker_thread="16006",
                worktree="/tmp/lane-stamp",
            ),
            work_key=K,
            ledger=led,
        )
        == D
    )
    stamp_error: list[str] = []

    def _run_stamp() -> None:
        try:
            stamp_registry_terminal_work_status(
                dispatch_id=D,
                terminal_status="completed",
                ledger=led,
            )
        except Exception as exc:  # noqa: BLE001
            stamp_error.append(repr(exc))

    c0 = sqlite3.connect(str(path), timeout=30)
    try:
        c0.execute("BEGIN IMMEDIATE")
        thread = threading.Thread(target=_run_stamp, daemon=True)
        thread.start()
        time.sleep(0.5)
        c0.execute(
            "UPDATE cursor_sdk_dispatches SET record_json=json_set(record_json, "
            "'$.overlap_marker', 'kept') WHERE dispatch_id=?",
            (D,),
        )
        c0.commit()
        thread.join(timeout=40)
        assert not thread.is_alive()
        assert stamp_error == []
    finally:
        c0.close()

    rec = _record(led)
    assert rec.get("overlap_marker") == "kept"
    assert rec.get("work_status") == "landed"
