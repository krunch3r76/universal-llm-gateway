"""Work-key admission gate — AC1–AC9, AC11, AC13 (offline)."""

from __future__ import annotations

import pytest
from implement_admission.impresario_policy import policy_snapshot_sha256
from universal_protocol.errors import ProtocolError

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
    generation_options: dict | None = None,
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
        generation_options=generation_options,
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
    second = _req(
        dispatch_id="d2", thread_id="10002", work_key=_WORK_KEY, contract="conductor"
    )
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


def _impresario_envelope(**overrides: object) -> dict[str, object]:
    env: dict[str, object] = {
        "house_root": "todo:impresario-test-house",
        "surface": "cursor-ide",
        "policy_sha256": policy_snapshot_sha256(),
    }
    env.update(overrides)
    return env


def test_conductor_empty_generation_options_raises_envelope_missing(
    isolated_ledger,
) -> None:
    req = _req(
        dispatch_id="cond-empty-go",
        work_key="todo:impresario-envelope-gate",
        contract="conductor",
        generation_options={},
    )
    with pytest.raises(ProtocolError) as excinfo:
        _admit(
            isolated_ledger,
            req,
            work_key=req.work_key,
            contract="conductor",
        )
    assert excinfo.value.code == "IMPRESARIO_ENVELOPE_MISSING"


def test_conductor_omitted_generation_options_admits(isolated_ledger) -> None:
    req = _req(
        dispatch_id="cond-no-go",
        work_key="todo:impresario-envelope-exempt",
        contract="conductor",
    )
    _admit(
        isolated_ledger,
        req,
        work_key=req.work_key,
        contract="conductor",
    )


def test_peer_held_beats_envelope_missing(isolated_ledger) -> None:
    key = "todo:impresario-peer-409"
    first = _req(dispatch_id="cond-peer-1", work_key=key, contract="conductor")
    _admit(isolated_ledger, first, work_key=key, contract="conductor")
    isolated_ledger.mark_running(dispatch_id="cond-peer-1")
    second = _req(
        dispatch_id="cond-peer-2",
        thread_id="10002",
        work_key=key,
        contract="conductor",
        generation_options={},
    )
    with pytest.raises(SourceRefConflict):
        _admit(isolated_ledger, second, work_key=key, contract="conductor")


def test_k2_lease_epoch_unchanged_when_k1_claimed(isolated_ledger) -> None:
    k1 = "todo:impresario-epoch-k1"
    k2 = "todo:impresario-epoch-k2"
    from services.git_integration_worker.cursor_sdk_work_claim import claim

    claim(k1, "seat-a", "L1", ledger=isolated_ledger)
    req = _req(dispatch_id="cond-k2", work_key=k2, contract="conductor")
    _admit(isolated_ledger, req, work_key=k2, contract="conductor")
    with isolated_ledger._connect() as conn:
        row = conn.execute(
            "SELECT lease_epoch FROM cursor_sdk_dispatches WHERE work_key=?",
            (k2,),
        ).fetchone()
    assert row is not None
    assert row["lease_epoch"] is None or int(row["lease_epoch"] or 0) <= 1
    with isolated_ledger._connect() as conn:
        k1_epoch = conn.execute(
            "SELECT MAX(COALESCE(lease_epoch, 0)) FROM cursor_sdk_dispatches "
            "WHERE work_key=?",
            (k1,),
        ).fetchone()[0]
        k2_epoch = conn.execute(
            "SELECT MAX(COALESCE(lease_epoch, 0)) FROM cursor_sdk_dispatches "
            "WHERE work_key=?",
            (k2,),
        ).fetchone()[0]
    assert int(k1_epoch or 0) >= 1
    assert int(k2_epoch or 0) <= 1
