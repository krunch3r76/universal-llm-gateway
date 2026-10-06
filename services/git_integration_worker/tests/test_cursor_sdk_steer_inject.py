"""Hermetic tests for GIW steer inject deposit / ack / escalation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from agent_bus_store import create_app
from agent_bus_store.auth import require_token
from agent_bus_store.turns_models import (
    MAX_LONG_TURN_BODY_CHARS,
    MAX_TURN_BODY_CHARS,
)
from fastapi.testclient import TestClient

from scripts.mcp_bridge_steer_inject import (
    PendingSteer,
    append_spool_entry,
    claim_pending,
    consume_next_steer_envelope,
    mark_delivered,
    read_delivery_ack,
)
from services.git_integration_worker.cursor_sdk_park_for_restart import ParkRefusal
from services.git_integration_worker.cursor_sdk_steer_inject import (
    SdkSteerInjectDelivered,
    SdkSteerInjectEscalated,
    SdkSteerInjectExpired,
    SdkSteerInjectSpooled,
    SteerDepositResult,
    deposit_steer_directive,
    escalate_idle_to_park,
    poll_delivery_ack,
    recover_undelivered_steer_from_thread,
)
from services.git_integration_worker.cursor_sdk_steer_inject_http import (
    inject_one_dispatch,
)
from services.git_integration_worker.cursor_sdk_steer_inject_preflight import (
    InjectRefusal,
    preflight_inject,
)
from services.git_integration_worker.cursor_sdk_supersede import (
    register_live_run,
    unregister_live_run,
)


def _ledger_admit(
    ledger: Any,
    *,
    dispatch_id: str,
    execution_id: str,
    thread_id: str = "10479",
    resume_of: str | None = None,
    message: str | None = None,
) -> None:
    from services.git_integration_worker.models.cursor_api import (
        CursorDispatchRequest,
        CursorDispatchResponse,
    )

    req = CursorDispatchRequest(
        thread_id=thread_id,
        model="cursor/composer-2.5",
        dispatch_id=dispatch_id,
        execution_id=execution_id,
        message=message or f"run-{dispatch_id}",
        resume_of=resume_of,
    )
    admission = CursorDispatchResponse(
        admitted=True,
        dispatch_id=dispatch_id,
        thread_id=thread_id,
        model_id="composer-2.5",
    )
    ledger.admit(
        req=req,
        fingerprint=ledger.fingerprint(req),
        execution_id=execution_id,
        caller_agent=None,
        resolved_model="composer-2.5",
        admission=admission,
        source_repo="/tmp/repo",
    )
    ledger.mark_running(dispatch_id=dispatch_id)


def _ledger_rowid(ledger: Any, dispatch_id: str) -> int:
    conn = ledger._connect()
    row = conn.execute(
        "SELECT rowid FROM cursor_sdk_dispatches WHERE dispatch_id=?",
        (dispatch_id,),
    ).fetchone()
    assert row is not None
    return int(row[0])


@pytest.fixture
def ledger_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    from services.git_integration_worker.cursor_dispatch_ledger import (
        CursorDispatchLedger,
    )

    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    CursorDispatchLedger._instance = None
    return CursorDispatchLedger.instance()


@pytest.fixture
def spool(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "steer-spool"
    root.mkdir()
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    return root


def test_deposit_writes_spool_and_emits(
    spool: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[Any] = []
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_steer_inject.emit_frontier_event",
        lambda ev: events.append(ev),
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_steer_inject._deposit_authority_turn",
        lambda **_k: "99",
    )
    result = deposit_steer_directive(
        dispatch_id="disp-dep",
        submitted_id="disp-dep",
        thread_id="10479",
        directive="check Stargate logs",
        reason="operator steer",
        spool_dir=spool,
    )
    assert result.authority_turn_id == "99"
    assert result.dispatch_id == "disp-dep"
    signals = [ev.signal for ev in events]
    assert "frontier.sdk.steer.inject.requested" in signals
    assert "frontier.sdk.steer.inject.spooled" in signals
    from scripts.mcp_bridge_steer_inject import spool_path

    assert spool_path(spool, "disp-dep").is_file()


def test_poll_delivery_ack_emits_delivered(
    spool: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[Any] = []
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_steer_inject.emit_frontier_event",
        lambda ev: events.append(ev),
    )
    deposit = SteerDepositResult(
        dispatch_id="disp-ack",
        entry_id="e-ack",
        authority_turn_id="12",
        spool_path=str(spool / "disp-ack.json"),
    )
    append_spool_entry(
        "disp-ack",
        authority_turn_id="12",
        directive="nudge",
        ttl_s=300,
        spool_dir=spool,
        entry_id="e-ack",
    )
    pending = PendingSteer(
        entry_id="e-ack",
        dispatch_id="disp-ack",
        authority_turn_id="12",
        directive="nudge",
        deposited_at="2026-09-13T00:00:00+00:00",
        ttl_s=300,
    )
    mark_delivered(pending, spool_dir=spool)
    row = poll_delivery_ack(deposit, spool_dir=spool)
    assert row is not None
    assert any(ev.signal == "frontier.sdk.steer.inject.delivered" for ev in events)


def test_escalate_idle_to_park_emits_and_signals_park(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[Any] = []
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_steer_inject.emit_frontier_event",
        lambda ev: events.append(ev),
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_steer_inject.signal_park",
        lambda *_a, **_k: MagicMock(
            dispatch_id="disp-esc",
            thread_id="10479",
            refusal=ParkRefusal.NOT_LIVE_HERE,
        ),
    )
    deposit = SteerDepositResult(
        dispatch_id="disp-esc",
        entry_id="e-esc",
        authority_turn_id="5",
        spool_path="/tmp/x",
    )
    escalate_idle_to_park(deposit, reason="idle without MCP tool-call ack")
    assert any(ev.signal == "frontier.sdk.steer.inject.escalated" for ev in events)


@pytest.mark.asyncio
async def test_inject_one_dispatch_returns_pending_handle(
    spool: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from services.git_integration_worker.cursor_dispatch_ledger import (
        CursorDispatchLedger,
    )
    from services.git_integration_worker.models.cursor_api import (
        CursorDispatchRequest,
        CursorDispatchResponse,
    )

    monkeypatch.setenv("DATA_DIR", str(spool.parent))
    CursorDispatchLedger._instance = None
    ledger = CursorDispatchLedger.instance()
    req = CursorDispatchRequest(
        thread_id="10479",
        model="cursor/composer-2.5",
        dispatch_id="disp-live",
        execution_id="exec-live",
        message="run",
    )
    admission = CursorDispatchResponse(
        admitted=True,
        dispatch_id="disp-live",
        thread_id="10479",
        model_id="composer-2.5",
    )
    ledger.admit(
        req=req,
        fingerprint=ledger.fingerprint(req),
        execution_id=req.execution_id,
        caller_agent=None,
        resolved_model="composer-2.5",
        admission=admission,
        source_repo="/tmp/repo",
    )
    ledger.mark_running(dispatch_id="disp-live")
    register_live_run(
        dispatch_id="disp-live",
        thread_id="10479",
        source_repo="/tmp/repo",
        run=object(),
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_steer_inject_http.deposit_steer_directive",
        lambda **_k: SteerDepositResult(
            dispatch_id="disp-live",
            entry_id="e-live",
            authority_turn_id="77",
            spool_path=str(spool / "disp-live.json"),
        ),
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_steer_inject_http.recover_undelivered_steer_from_thread",
        lambda **_k: [],
    )
    status, body = await inject_one_dispatch(
        dispatch_id="disp-live",
        directive="check logs",
        reason="operator steer",
        actor="cursor",
        ttl_s=300,
    )
    unregister_live_run(dispatch_id="disp-live")
    assert status == 202
    assert body["inject_state"] == "pending"
    assert body["execution_id"] == "exec-live"
    assert body["steer"] == "inject"
    assert "park_kind" not in body


@pytest.mark.asyncio
async def test_inject_preflight_not_found() -> None:
    pre = preflight_inject("does-not-exist")
    assert pre.refusal is InjectRefusal.NOT_FOUND


@pytest.mark.asyncio
async def test_inject_preflight_not_live(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from services.git_integration_worker.cursor_dispatch_ledger import (
        CursorDispatchLedger,
    )
    from services.git_integration_worker.models.cursor_api import (
        CursorDispatchRequest,
        CursorDispatchResponse,
    )

    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    CursorDispatchLedger._instance = None
    ledger = CursorDispatchLedger.instance()
    req = CursorDispatchRequest(
        thread_id="10479",
        model="cursor/composer-2.5",
        dispatch_id="disp-idle",
        execution_id="exec-idle",
        message="run",
    )
    admission = CursorDispatchResponse(
        admitted=True,
        dispatch_id="disp-idle",
        thread_id="10479",
        model_id="composer-2.5",
    )
    ledger.admit(
        req=req,
        fingerprint=ledger.fingerprint(req),
        execution_id=req.execution_id,
        caller_agent=None,
        resolved_model="composer-2.5",
        admission=admission,
        source_repo="/tmp/repo",
    )
    ledger.mark_running(dispatch_id="disp-idle")
    pre = preflight_inject("disp-idle")
    assert pre.refusal is None
    assert pre.row is not None
    assert pre.row["dispatch_id"] == "disp-idle"


@pytest.mark.asyncio
async def test_inject_one_dispatch_missing_directive_422() -> None:
    status, body = await inject_one_dispatch(
        dispatch_id="disp-any",
        directive="   ",
        reason="test",
        actor="cursor",
        ttl_s=None,
    )
    assert status == 422
    assert body["code"] == "CURSOR_INJECT_DIRECTIVE_REQUIRED"


def test_recover_undelivered_steer_from_thread_re_spools(
    spool: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_steer_inject._fetch_thread_turns",
        lambda _tid: [
            {
                "turn_number": 55,
                "subject": "STEER directive",
                "body": (
                    '{"dispatch_id":"disp-rec","directive":"resume harvest",'
                    '"reason":"bridge restart","actor":"steer-inject"}'
                ),
            }
        ],
    )
    recovered = recover_undelivered_steer_from_thread(
        dispatch_id="disp-rec",
        thread_id="10479",
        spool_dir=spool,
    )
    assert len(recovered) == 1
    assert recovered[0].authority_turn_id == "55"
    from scripts.mcp_bridge_steer_inject import claim_pending

    pending = claim_pending("disp-rec", spool_dir=spool)
    assert pending is not None
    assert pending.directive == "resume harvest"


class _StoreClient:
    def __init__(self, client: TestClient) -> None:
        self._client = client
        self.posts: list[Any] = []

    def __enter__(self) -> _StoreClient:
        return self

    def __exit__(self, *_exc: object) -> bool:
        return False

    def post(self, path: str, json: dict | None = None, headers: dict | None = None):
        resp = self._client.post(path, json=json, headers=headers)
        self.posts.append(resp)
        return resp

    def get(self, path: str, params: dict | None = None, headers: dict | None = None):
        return self._client.get(path, params=params, headers=headers)


def test_deposit_4442_directive_stores_inline_and_recovers(
    spool: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """4442-char directive stays JSON on POST /turns. No spill, no sidecar."""
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_steer_inject.emit_frontier_event",
        lambda _ev: None,
    )
    cortex_root = tmp_path / "cortex-files"
    cortex_root.mkdir()
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(cortex_root))
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(tmp_path / "bus.db"))
    import cortex_store.dispatch_ops._thread_sidecar as sidecar_mod

    monkeypatch.setattr(sidecar_mod, "_FILES_ROOT", cortex_root)
    app = create_app(db_path=str(tmp_path / "bus.db"))
    app.dependency_overrides[require_token] = lambda: None
    directive = "d" * 4442
    with TestClient(app) as client:
        seed = client.post(
            "/threads/with-turn",
            json={
                "slug": "steer-inline-seed",
                "from": "cursor",
                "to": "web",
                "subject": "seed",
                "body": "hello",
            },
        )
        assert seed.status_code == 201, seed.text
        thread_id = seed.json()["thread"]["id"]
        store = _StoreClient(client)
        monkeypatch.setattr(
            "services.git_integration_worker.cursor_sdk_steer_inject.make_sync_client",
            lambda *_a, **_k: store,
        )
        result = deposit_steer_directive(
            dispatch_id="disp-4442",
            submitted_id="disp-4442",
            thread_id=thread_id,
            directive=directive,
            reason="operator steer",
            spool_dir=spool,
        )
        assert store.posts, "deposit did not POST /turns"
        created = store.posts[0]
        assert created.status_code == 201, created.text
        created_body = created.json()
        assert not created_body.get("auto_spilled")
        assert created_body.get("sidecar_uri") is None
        posted = client.get(
            f"/turns/by-number?thread={thread_id}&turn_number={result.authority_turn_id}"
        )
        assert posted.status_code == 200, posted.text
        payload = posted.json()
        assert not payload.get("auto_spilled")
        assert payload.get("sidecar_uri") is None
        parsed = json.loads(payload["body"])
        assert parsed["directive"] == directive
        from scripts.mcp_bridge_steer_inject import claim_pending, spool_path

        spool_path(spool, "disp-4442").unlink()
        recovered = recover_undelivered_steer_from_thread(
            dispatch_id="disp-4442",
            thread_id=thread_id,
            spool_dir=spool,
        )
        pending = claim_pending("disp-4442", spool_dir=spool)
    assert len(recovered) == 1
    assert pending is not None
    assert pending.directive == directive


def test_deposit_over_8k_stores_inline_and_recovers(
    spool: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A steer JSON body past 8k and under 64k stays inline and recoverable."""
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_steer_inject.emit_frontier_event",
        lambda _ev: None,
    )
    cortex_root = tmp_path / "cortex-files"
    cortex_root.mkdir()
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(cortex_root))
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(tmp_path / "bus.db"))
    import cortex_store.dispatch_ops._thread_sidecar as sidecar_mod

    monkeypatch.setattr(sidecar_mod, "_FILES_ROOT", cortex_root)
    app = create_app(db_path=str(tmp_path / "bus.db"))
    app.dependency_overrides[require_token] = lambda: None
    directive = "d" * (MAX_TURN_BODY_CHARS + 1)
    with TestClient(app) as client:
        seed = client.post(
            "/threads/with-turn",
            json={
                "slug": "steer-over-8k-seed",
                "from": "cursor",
                "to": "web",
                "subject": "seed",
                "body": "hello",
            },
        )
        assert seed.status_code == 201, seed.text
        thread_id = seed.json()["thread"]["id"]
        store = _StoreClient(client)
        monkeypatch.setattr(
            "services.git_integration_worker.cursor_sdk_steer_inject.make_sync_client",
            lambda *_a, **_k: store,
        )
        result = deposit_steer_directive(
            dispatch_id="disp-over8k",
            submitted_id="disp-over8k",
            thread_id=thread_id,
            directive=directive,
            reason="operator steer",
            spool_dir=spool,
        )
        created = store.posts[0]
        assert created.status_code == 201, created.text
        assert not created.json().get("auto_spilled")
        assert created.json().get("sidecar_uri") is None
        posted = client.get(
            f"/turns/by-number?thread={thread_id}&turn_number={result.authority_turn_id}"
        )
        assert posted.status_code == 200, posted.text
        payload = posted.json()
        assert MAX_TURN_BODY_CHARS < len(payload["body"]) <= MAX_LONG_TURN_BODY_CHARS
        assert not payload.get("auto_spilled")
        assert payload.get("sidecar_uri") is None
        parsed = json.loads(payload["body"])
        assert parsed["directive"] == directive
        from scripts.mcp_bridge_steer_inject import claim_pending, spool_path

        spool_path(spool, "disp-over8k").unlink()
        recovered = recover_undelivered_steer_from_thread(
            dispatch_id="disp-over8k",
            thread_id=thread_id,
            spool_dir=spool,
        )
        pending = claim_pending("disp-over8k", spool_dir=spool)
    assert len(recovered) == 1
    assert pending is not None
    assert pending.directive == directive


def test_deposit_over_64k_raises_before_spool(
    spool: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Past 64k the allow_long lane returns 413 and the directive is not spooled."""
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_steer_inject.emit_frontier_event",
        lambda _ev: None,
    )
    cortex_root = tmp_path / "cortex-files"
    cortex_root.mkdir()
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(cortex_root))
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(tmp_path / "bus.db"))
    import cortex_store.dispatch_ops._thread_sidecar as sidecar_mod

    monkeypatch.setattr(sidecar_mod, "_FILES_ROOT", cortex_root)
    app = create_app(db_path=str(tmp_path / "bus.db"))
    app.dependency_overrides[require_token] = lambda: None
    directive = "d" * (MAX_LONG_TURN_BODY_CHARS + 1)
    with TestClient(app) as client:
        seed = client.post(
            "/threads/with-turn",
            json={
                "slug": "steer-over-64k-seed",
                "from": "cursor",
                "to": "web",
                "subject": "seed",
                "body": "hello",
            },
        )
        assert seed.status_code == 201, seed.text
        thread_id = seed.json()["thread"]["id"]
        store = _StoreClient(client)
        monkeypatch.setattr(
            "services.git_integration_worker.cursor_sdk_steer_inject.make_sync_client",
            lambda *_a, **_k: store,
        )
        with pytest.raises(RuntimeError, match="413"):
            deposit_steer_directive(
                dispatch_id="disp-over64k",
                submitted_id="disp-over64k",
                thread_id=thread_id,
                directive=directive,
                reason="operator steer",
                spool_dir=spool,
            )
        assert store.posts, "deposit did not POST /turns"
        created = store.posts[0]
        assert created.status_code == 413, created.text
        assert created.json()["detail"]["reason"] == "body_too_large"
    from scripts.mcp_bridge_steer_inject import spool_path

    assert not spool_path(spool, "disp-over64k").is_file()


def _seed_ac1_live(ledger: Any) -> None:
    _ledger_admit(ledger, dispatch_id="D", execution_id="X")
    register_live_run(
        dispatch_id="D",
        thread_id="10479",
        source_repo="/tmp/repo",
        run=object(),
    )


def test_preflight_ac1_plain_dispatch_id(ledger_env: Any) -> None:
    _seed_ac1_live(ledger_env)
    try:
        pre = preflight_inject("D")
        assert pre.refusal is None
        assert pre.row is not None
        assert pre.row["dispatch_id"] == "D"
    finally:
        unregister_live_run(dispatch_id="D")


def test_preflight_ac2_bare_execution_id(ledger_env: Any) -> None:
    _seed_ac1_live(ledger_env)
    try:
        pre = preflight_inject("X")
        assert pre.refusal is None
        assert pre.row["dispatch_id"] == "D"
    finally:
        unregister_live_run(dispatch_id="D")


def test_preflight_ac3_prefixed_execution_id(ledger_env: Any) -> None:
    _seed_ac1_live(ledger_env)
    try:
        pre = preflight_inject("cursor-sdk:dispatch:X")
        assert pre.refusal is None
        assert pre.row["dispatch_id"] == "D"
    finally:
        unregister_live_run(dispatch_id="D")


def _seed_ac4_park_resume(ledger: Any) -> None:
    _ledger_admit(ledger, dispatch_id="P", execution_id="E")
    ledger.mark_terminal(dispatch_id="P", terminal_status="cancelled")
    _ledger_admit(ledger, dispatch_id="P-r1", execution_id="E", resume_of="P")
    register_live_run(
        dispatch_id="P-r1",
        thread_id="10479",
        source_repo="/tmp/repo",
        run=object(),
    )


def test_preflight_ac4_park_resume_child_wins(ledger_env: Any) -> None:
    _seed_ac4_park_resume(ledger_env)
    try:
        pre = preflight_inject("E")
        assert pre.refusal is None
        assert pre.row["dispatch_id"] == "P-r1"
    finally:
        unregister_live_run(dispatch_id="P-r1")


def test_preflight_ac5_prefixed_park_resume(ledger_env: Any) -> None:
    _seed_ac4_park_resume(ledger_env)
    try:
        pre = preflight_inject("cursor-sdk:dispatch:E")
        assert pre.refusal is None
        assert pre.row["dispatch_id"] == "P-r1"
    finally:
        unregister_live_run(dispatch_id="P-r1")


def test_preflight_ac6_rowid_order_irrelevant(ledger_env: Any) -> None:
    _ledger_admit(ledger_env, dispatch_id="P", execution_id="E")
    ledger_env.mark_terminal(dispatch_id="P", terminal_status="cancelled")
    _ledger_admit(ledger_env, dispatch_id="P-r1", execution_id="E", resume_of="P")
    register_live_run(
        dispatch_id="P-r1",
        thread_id="10479",
        source_repo="/tmp/repo",
        run=object(),
    )
    _ledger_admit(ledger_env, dispatch_id="P-r2", execution_id="E", resume_of="P")
    ledger_env.mark_terminal(dispatch_id="P-r2", terminal_status="cancelled")
    assert _ledger_rowid(ledger_env, "P-r2") > _ledger_rowid(ledger_env, "P-r1")
    try:
        pre = preflight_inject("E")
        assert pre.refusal is None
        assert pre.row["dispatch_id"] == "P-r1"
    finally:
        unregister_live_run(dispatch_id="P-r1")


@pytest.mark.asyncio
async def test_preflight_ac7_ambiguous_two_live(
    ledger_env: Any, spool: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ledger_admit(ledger_env, dispatch_id="P-r1", execution_id="E", thread_id="10479")
    register_live_run(
        dispatch_id="P-r1",
        thread_id="10479",
        source_repo="/tmp/repo",
        run=object(),
    )
    _ledger_admit(ledger_env, dispatch_id="P-r2", execution_id="E", thread_id="10480")
    register_live_run(
        dispatch_id="P-r2",
        thread_id="10480",
        source_repo="/tmp/repo",
        run=object(),
    )
    deposit_calls: list[dict[str, Any]] = []
    events: list[Any] = []
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_steer_inject_http.deposit_steer_directive",
        lambda **kw: (
            deposit_calls.append(kw)
            or SteerDepositResult(
                dispatch_id="x",
                entry_id="e",
                authority_turn_id="1",
                spool_path=str(spool / "x.json"),
            )
        ),
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_steer_inject.emit_frontier_event",
        lambda ev: events.append(ev),
    )
    try:
        pre = preflight_inject("E")
        assert pre.refusal is InjectRefusal.NOT_LIVE
        assert pre.row is None
        assert pre.detail is not None
        assert pre.detail.startswith("ambiguous execution_id")

        status, body = await inject_one_dispatch(
            dispatch_id="E",
            directive="nudge",
            reason="test",
            actor="cursor",
            ttl_s=300,
        )
        assert status == 409
        assert body["code"] == "CURSOR_INJECT_NOT_LIVE"
        assert body["message"].startswith("ambiguous execution_id")
        assert not deposit_calls
        from scripts.mcp_bridge_steer_inject import spool_path

        assert not spool_path(spool, "E").is_file()
        assert not any(
            ev.signal == "frontier.sdk.steer.inject.requested" for ev in events
        )
    finally:
        unregister_live_run(dispatch_id="P-r1")
        unregister_live_run(dispatch_id="P-r2")


def test_preflight_ac8_cancelled_parent(ledger_env: Any) -> None:
    _seed_ac4_park_resume(ledger_env)
    unregister_live_run(dispatch_id="P-r1")
    try:
        pre = preflight_inject("P")
        assert pre.refusal is InjectRefusal.NOT_LIVE
        assert pre.row is not None
        assert pre.row["dispatch_id"] == "P"
        assert pre.detail == "row status='cancelled'"
    finally:
        pass


def test_preflight_ac9_not_found(ledger_env: Any) -> None:
    pre = preflight_inject("no-such")
    assert pre.refusal is InjectRefusal.NOT_FOUND
    assert pre.row is None


def test_preflight_ac10_execution_id_not_live(ledger_env: Any) -> None:
    _ledger_admit(ledger_env, dispatch_id="D", execution_id="X")
    pre = preflight_inject("X")
    assert pre.refusal is None
    assert pre.row is not None
    assert pre.row["dispatch_id"] == "D"


@pytest.mark.asyncio
async def test_inject_admit_grace_spool_before_live_register(
    ledger_env: Any, spool: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Admitted row deposits before register_live_run; bridge consumer delivers once."""
    _ledger_admit(ledger_env, dispatch_id="disp-grace", execution_id="exec-grace")
    monkeypatch.setenv("CURSOR_SDK_DISPATCH_LEDGER_ENV", str(ledger_env._db_path))
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_steer_inject._deposit_authority_turn",
        lambda **_k: "41",
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_steer_inject.emit_frontier_event",
        lambda _ev: None,
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_steer_inject.steer_spool_dir",
        lambda: spool,
    )
    pre = preflight_inject("disp-grace")
    assert pre.refusal is None
    status, body = await inject_one_dispatch(
        dispatch_id="disp-grace",
        directive="hold the ruling",
        reason="admit grace",
        actor="cursor",
        ttl_s=300,
    )
    assert status == 202
    assert body["inject_state"] == "pending"
    from scripts.mcp_bridge_steer_inject import spool_path

    assert spool_path(spool, "disp-grace").is_file()
    envelope = consume_next_steer_envelope(
        "disp-grace", spool_dir=spool, delivered_via="mcp_bridge"
    )
    assert envelope is not None
    assert "hold the ruling" in envelope
    assert consume_next_steer_envelope("disp-grace", spool_dir=spool) is None
    ack = read_delivery_ack("disp-grace", str(body["entry_id"]), spool_dir=spool)
    assert ack is not None
    assert ack.get("delivered_via") == "mcp_bridge"


@pytest.mark.asyncio
async def test_inject_missing_thread_retryable(
    ledger_env: Any,
) -> None:
    _ledger_admit(ledger_env, dispatch_id="disp-nothread", execution_id="exec-nt")
    conn = ledger_env._connect()
    conn.execute(
        "UPDATE cursor_sdk_dispatches SET thread_id='' WHERE dispatch_id=?",
        ("disp-nothread",),
    )
    conn.commit()
    pre = preflight_inject("disp-nothread")
    assert pre.refusal is InjectRefusal.AWAITING_THREAD
    status, body = await inject_one_dispatch(
        dispatch_id="disp-nothread",
        directive="wait",
        reason="test",
        actor="cursor",
        ttl_s=300,
    )
    assert status == 503
    assert body["retryable"] is True
    assert body["code"] == "CURSOR_INJECT_AWAITING_THREAD"
    assert body["data"]["retry_after_s"] == 5


def test_preflight_ac11_pk_beats_execution_id_collision(ledger_env: Any) -> None:
    _ledger_admit(ledger_env, dispatch_id="D", execution_id="not-D")
    register_live_run(
        dispatch_id="D",
        thread_id="10479",
        source_repo="/tmp/repo",
        run=object(),
    )
    _ledger_admit(
        ledger_env, dispatch_id="other-live", execution_id="D", thread_id="10480"
    )
    register_live_run(
        dispatch_id="other-live",
        thread_id="10480",
        source_repo="/tmp/repo",
        run=object(),
    )
    try:
        pre = preflight_inject("D")
        assert pre.refusal is None
        assert pre.row["dispatch_id"] == "D"
    finally:
        unregister_live_run(dispatch_id="D")
        unregister_live_run(dispatch_id="other-live")


def test_preflight_double_prefix_not_found(ledger_env: Any) -> None:
    _ledger_admit(ledger_env, dispatch_id="row-x", execution_id="X")
    register_live_run(
        dispatch_id="row-x",
        thread_id="10479",
        source_repo="/tmp/repo",
        run=object(),
    )
    try:
        pre = preflight_inject("cursor-sdk:dispatch:cursor-sdk:dispatch:X")
        assert pre.refusal is InjectRefusal.NOT_FOUND
    finally:
        unregister_live_run(dispatch_id="row-x")


@pytest.mark.asyncio
async def test_inject_ac12_bus_address_202(
    spool: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from services.git_integration_worker.cursor_dispatch_ledger import (
        CursorDispatchLedger,
    )

    monkeypatch.setenv("DATA_DIR", str(spool.parent))
    CursorDispatchLedger._instance = None
    ledger = CursorDispatchLedger.instance()
    _ledger_admit(ledger, dispatch_id="disp-live", execution_id="exec-live")
    register_live_run(
        dispatch_id="disp-live",
        thread_id="10479",
        source_repo="/tmp/repo",
        run=object(),
    )
    deposit_kw: list[dict[str, Any]] = []
    recover_kw: list[dict[str, Any]] = []
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_steer_inject_http.deposit_steer_directive",
        lambda **kw: (
            deposit_kw.append(kw)
            or SteerDepositResult(
                dispatch_id="disp-live",
                entry_id="e-live",
                authority_turn_id="77",
                spool_path=str(spool / "disp-live.json"),
            )
        ),
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_steer_inject_http.recover_undelivered_steer_from_thread",
        lambda **kw: recover_kw.append(kw) or [],
    )
    status, body = await inject_one_dispatch(
        dispatch_id="cursor-sdk:dispatch:exec-live",
        directive="check logs",
        reason="operator steer",
        actor="cursor",
        ttl_s=300,
    )
    unregister_live_run(dispatch_id="disp-live")
    assert status == 202
    assert body["dispatch_id"] == "disp-live"
    assert body["execution_id"] == "exec-live"
    assert body["inject_state"] == "pending"
    assert deposit_kw[0]["dispatch_id"] == "disp-live"
    assert deposit_kw[0]["submitted_id"] == "cursor-sdk:dispatch:exec-live"
    assert recover_kw[0]["dispatch_id"] == "disp-live"


@pytest.mark.asyncio
async def test_inject_ac13_not_found_envelope() -> None:
    status, body = await inject_one_dispatch(
        dispatch_id="no-such",
        directive="x",
        reason="test",
        actor="cursor",
        ttl_s=300,
    )
    assert status == 404
    assert body["code"] == "CURSOR_INJECT_NOT_FOUND"
    assert body["data"]["dispatch_id"] == "no-such"
    assert body["message"] == "inject refused: NOT_FOUND: no-such"


@pytest.mark.asyncio
async def test_inject_ac14_ambiguous_prefixed_409(ledger_env: Any) -> None:
    _ledger_admit(ledger_env, dispatch_id="P-r1", execution_id="E", thread_id="10479")
    register_live_run(
        dispatch_id="P-r1",
        thread_id="10479",
        source_repo="/tmp/repo",
        run=object(),
    )
    _ledger_admit(ledger_env, dispatch_id="P-r2", execution_id="E", thread_id="10480")
    register_live_run(
        dispatch_id="P-r2",
        thread_id="10480",
        source_repo="/tmp/repo",
        run=object(),
    )
    try:
        status, body = await inject_one_dispatch(
            dispatch_id="cursor-sdk:dispatch:E",
            directive="nudge",
            reason="test",
            actor="cursor",
            ttl_s=300,
        )
        assert status == 409
        assert body["code"] == "CURSOR_INJECT_NOT_LIVE"
        assert body["message"].startswith("ambiguous execution_id")
        assert body["data"]["dispatch_id"] == "cursor-sdk:dispatch:E"
    finally:
        unregister_live_run(dispatch_id="P-r1")
        unregister_live_run(dispatch_id="P-r2")


@pytest.mark.asyncio
async def test_inject_ac15_cancelled_parent_409(ledger_env: Any) -> None:
    _seed_ac4_park_resume(ledger_env)
    unregister_live_run(dispatch_id="P-r1")
    status, body = await inject_one_dispatch(
        dispatch_id="P",
        directive="nudge",
        reason="test",
        actor="cursor",
        ttl_s=300,
    )
    assert status == 409
    assert body["code"] == "CURSOR_INJECT_NOT_LIVE"
    assert body["data"]["dispatch_id"] == "P"


@pytest.mark.asyncio
async def test_inject_ac17_ac18_submitted_id_on_event(
    ledger_env: Any, monkeypatch: pytest.MonkeyPatch, spool: Path
) -> None:
    _seed_ac4_park_resume(ledger_env)
    events: list[Any] = []
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_steer_inject.emit_frontier_event",
        lambda ev: events.append(ev),
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_steer_inject._deposit_authority_turn",
        lambda **_k: "99",
    )
    try:
        deposit_steer_directive(
            dispatch_id="P-r1",
            submitted_id="cursor-sdk:dispatch:E",
            thread_id="10479",
            directive="steer",
            reason="test",
            spool_dir=spool,
        )
        requested = [
            ev for ev in events if ev.signal == "frontier.sdk.steer.inject.requested"
        ][0]
        assert requested.payload["submitted_id"] == "cursor-sdk:dispatch:E"
        assert requested.payload["dispatch_id"] == "P-r1"
        assert requested.payload["submitted_id"] is not None

        events.clear()
        deposit_steer_directive(
            dispatch_id="plain-key",
            submitted_id="plain-key",
            thread_id="10479",
            directive="steer",
            reason="test",
            spool_dir=spool,
        )
        req2 = [
            ev for ev in events if ev.signal == "frontier.sdk.steer.inject.requested"
        ][0]
        assert req2.payload["submitted_id"] == "plain-key"
        assert req2.payload["submitted_id"] == req2.payload["dispatch_id"]
    finally:
        unregister_live_run(dispatch_id="P-r1")


def test_a1_terminal_pending_expires_into_closeout(
    spool: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[Any] = []
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_steer_inject.emit_frontier_event",
        lambda ev: events.append(ev),
    )
    append_spool_entry(
        "disp-a1",
        authority_turn_id="5",
        directive="lost ruling",
        ttl_s=300,
        spool_dir=spool,
        entry_id="entry-a1",
    )
    from services.git_integration_worker.cursor_sdk_steer_inject import (
        TERMINAL_UNDELIVERED_REASON,
        apply_steer_undelivered_closeout,
    )

    body = json.dumps({"status": "complete"})
    out = apply_steer_undelivered_closeout(body, dispatch_id="disp-a1", spool_dir=spool)
    payload = json.loads(out)
    assert payload["steer_undelivered"] == [
        {
            "entry_id": "entry-a1",
            "deposited_at": payload["steer_undelivered"][0]["deposited_at"],
            "reason": TERMINAL_UNDELIVERED_REASON,
        }
    ]
    assert payload["steer_undelivered"][0]["deposited_at"]
    expired = [ev for ev in events if ev.signal == "frontier.sdk.steer.inject.expired"]
    assert len(expired) == 1
    assert expired[0].payload["entry_id"] == "entry-a1"
    assert expired[0].payload["reason"] == TERMINAL_UNDELIVERED_REASON
    from scripts.mcp_bridge_steer_inject import spool_path

    stored = json.loads(spool_path(spool, "disp-a1").read_text(encoding="utf-8"))
    assert stored["pending"] == []
    assert stored["expired"][0]["reason"] == TERMINAL_UNDELIVERED_REASON


def test_a2_terminal_ttl_expired_pending_same(
    spool: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[Any] = []
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_steer_inject.emit_frontier_event",
        lambda ev: events.append(ev),
    )
    append_spool_entry(
        "disp-a2",
        authority_turn_id="6",
        directive="aged ruling",
        ttl_s=300,
        spool_dir=spool,
        entry_id="entry-a2",
    )
    from scripts.mcp_bridge_steer_inject import spool_path

    path = spool_path(spool, "disp-a2")
    data = json.loads(path.read_text(encoding="utf-8"))
    data["pending"][0]["deposited_at"] = "2020-01-01T00:00:00+00:00"
    data["pending"][0]["ttl_s"] = 1
    path.write_text(json.dumps(data), encoding="utf-8")
    assert claim_pending("disp-a2", spool_dir=spool) is None
    from services.git_integration_worker.cursor_sdk_steer_inject import (
        TERMINAL_UNDELIVERED_REASON,
        apply_steer_undelivered_closeout,
    )

    out = apply_steer_undelivered_closeout(
        json.dumps({"status": "complete"}),
        dispatch_id="disp-a2",
        spool_dir=spool,
    )
    payload = json.loads(out)
    assert payload["steer_undelivered"][0]["entry_id"] == "entry-a2"
    assert payload["steer_undelivered"][0]["reason"] == TERMINAL_UNDELIVERED_REASON
    assert any(
        ev.signal == "frontier.sdk.steer.inject.expired"
        and ev.payload["entry_id"] == "entry-a2"
        for ev in events
    )


def test_a3_delivered_closeout_omits_steer_undelivered(
    spool: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[Any] = []
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_steer_inject.emit_frontier_event",
        lambda ev: events.append(ev),
    )
    append_spool_entry(
        "disp-a3",
        authority_turn_id="7",
        directive="already there",
        ttl_s=300,
        spool_dir=spool,
        entry_id="entry-a3",
    )
    pending = claim_pending("disp-a3", spool_dir=spool)
    assert pending is not None
    mark_delivered(pending, spool_dir=spool)
    from services.git_integration_worker.cursor_sdk_steer_inject import (
        apply_steer_undelivered_closeout,
    )

    body = json.dumps({"status": "complete"})
    out = apply_steer_undelivered_closeout(body, dispatch_id="disp-a3", spool_dir=spool)
    assert out == body
    assert "steer_undelivered" not in out
    assert events == []


def test_prose_closeout_gains_steer_trailer(
    spool: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """check-review prose (not a JSON object) still records undelivered steers."""
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_steer_inject.emit_frontier_event",
        lambda _ev: None,
    )
    append_spool_entry(
        "disp-prose",
        authority_turn_id="5",
        directive="lost",
        ttl_s=300,
        spool_dir=spool,
        entry_id="entry-prose",
    )
    from services.git_integration_worker.cursor_sdk_steer_inject import (
        apply_steer_undelivered_closeout,
    )

    out = apply_steer_undelivered_closeout(
        "findings text\n", dispatch_id="disp-prose", spool_dir=spool
    )
    assert out.startswith("findings text\n")
    assert "STEER_UNDELIVERED:" in out
    assert "entry-prose" in out


def test_ac19_sibling_payloads_omit_submitted_id() -> None:
    sp = SdkSteerInjectSpooled(
        dispatch_id="d",
        thread_id="t",
        entry_id="e",
        authority_turn_id="a",
        spool_uri="u",
    )
    de = SdkSteerInjectDelivered(dispatch_id="d", entry_id="e", authority_turn_id="a")
    ex = SdkSteerInjectExpired(dispatch_id="d", entry_id="e", ttl_s=1)
    es = SdkSteerInjectEscalated(dispatch_id="d", entry_id="e", reason="r")
    for ev in (sp, de, ex, es):
        assert "submitted_id" not in ev.payload
