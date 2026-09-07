"""Work-key admission gate — AC1–AC9, AC11, AC13 (offline)."""

from __future__ import annotations

import os
import sqlite3
from unittest.mock import patch

import pytest

from services.git_integration_worker.cursor_dispatch_ledger import (
    CursorDispatchLedger,
    RemintCapExceeded,
    SourceRefConflict,
)
from services.git_integration_worker.cursor_sdk_work_key_gate import (
    compute_write_class,
    derive_adhoc_work_key,
    gate_mode,
    is_root_row,
    validate_work_key_scheme,
)
from services.git_integration_worker.models.cursor_api import CursorDispatchRequest

pytestmark = pytest.mark.offline

_WORK_KEY = "todo:work-key-gate-fixture"


def _req(
    *,
    dispatch_id: str,
    thread_id: str = "10001",
    work_key: str | None = None,
    force: bool = False,
    contract: str = "implement",
) -> CursorDispatchRequest:
    return CursorDispatchRequest(
        thread_id=thread_id,
        model="composer-2.5",
        dispatch_id=dispatch_id,
        execution_id=f"exec-{dispatch_id}",
        packet_path="tmp/packet.md",
        handoff_contract=contract,
        work_key=work_key,
        force=force,
    )


def _admit(
    ledger: CursorDispatchLedger,
    req: CursorDispatchRequest,
    *,
    work_key: str | None = None,
    identity_class: str = "declared",
    contract: str = "implement",
    force: bool = False,
) -> None:
    from services.git_integration_worker.models.cursor_api import CursorDispatchResponse

    ledger.admit(
        req=req,
        fingerprint=ledger.fingerprint(req),
        execution_id=req.execution_id,
        caller_agent="cursor",
        resolved_model=req.model,
        admission=CursorDispatchResponse(
            admitted=True,
            dispatch_id=req.dispatch_id,
            thread_id=req.thread_id,
            model_id=req.model,
        ),
        contract=contract,
        work_key=work_key,
        identity_class=identity_class,
        force=force,
    )


@pytest.fixture()
def isolated_ledger():
    from services.git_integration_worker.cursor_dispatch_ledger import _connect

    CursorDispatchLedger._instance = None
    ledger = CursorDispatchLedger.instance()
    with _connect() as conn:
        conn.execute("DELETE FROM cursor_sdk_dispatches")
    yield ledger
    CursorDispatchLedger._instance = None


def test_gate_mode_defaults_observe(monkeypatch) -> None:
    monkeypatch.delenv("CURSOR_SDK_WORK_KEY_GATE_MODE", raising=False)
    assert gate_mode() == "observe"


def test_validate_work_key_scheme_ac4() -> None:
    assert validate_work_key_scheme("todo:x")
    assert validate_work_key_scheme("agent-bus:10223/spec-leg")
    assert validate_work_key_scheme("friction:32553")
    assert not validate_work_key_scheme("foo")


def test_compute_write_class_adhoc_readonly() -> None:
    assert not compute_write_class(read_only=True, lane="A", contract="none")
    assert compute_write_class(read_only=False, lane="B", contract="none")


def test_adhoc_key_derivation() -> None:
    key = derive_adhoc_work_key("abcdef0123456789abcdef0123456789")
    assert key.startswith("adhoc:")
    assert len(key) == len("adhoc:") + 16


def test_same_work_key_conductor_rejected_ac5(isolated_ledger) -> None:
    first = _req(dispatch_id="d1", work_key=_WORK_KEY, contract="conductor")
    second = _req(dispatch_id="d2", thread_id="10002", work_key=_WORK_KEY, contract="conductor")
    _admit(isolated_ledger, first, work_key=_WORK_KEY, contract="conductor")
    isolated_ledger.mark_running(dispatch_id="d1")
    with pytest.raises(SourceRefConflict) as excinfo:
        _admit(isolated_ledger, second, work_key=_WORK_KEY, contract="conductor")
    assert excinfo.value.holder_kind == "conductor"
    assert excinfo.value.steer is not None


def test_adhoc_excluded_from_gate2(isolated_ledger) -> None:
    wf = isolated_ledger.work_fingerprint(_req(dispatch_id="a1"))
    key = derive_adhoc_work_key(wf or "abc")
    r1 = _req(dispatch_id="a1", work_key=key, contract="none")
    r2 = _req(dispatch_id="a2", thread_id="10002", work_key=key, contract="none")
    _admit(isolated_ledger, r1, work_key=key, identity_class="adhoc", contract="none")
    _admit(isolated_ledger, r2, work_key=key, identity_class="adhoc", contract="none")


def test_remint_cap_ac9(isolated_ledger, monkeypatch) -> None:
    monkeypatch.setenv("CURSOR_SDK_WORK_KEY_SEQ_CAP", "2")
    for i in range(2):
        rid = f"r{i}"
        req = _req(dispatch_id=rid, work_key=_WORK_KEY)
        _admit(isolated_ledger, req, work_key=_WORK_KEY)
        isolated_ledger.mark_terminal(dispatch_id=rid, terminal_status="completed")
    third = _req(dispatch_id="r2", work_key=_WORK_KEY)
    with pytest.raises(RemintCapExceeded):
        _admit(isolated_ledger, third, work_key=_WORK_KEY)


def test_root_predicate_ac13() -> None:
    assert is_root_row(resume_of=None, nest_under=None, hop_from=None)
    assert not is_root_row(resume_of="p", nest_under=None, hop_from=None)
    assert not is_root_row(resume_of=None, nest_under=None, hop_from="h")
