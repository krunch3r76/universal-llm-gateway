"""Acceptance and boundary tests for D18 registry-decided CSE holder projection."""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from io import BytesIO
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.git_integration_worker import cse_session_holders
from services.git_integration_worker.cse_session_holders import (
    RemoteUpsert,
    ensure_schema,
    get_holder,
    resolve_holder_by_registration,
    upsert_holder,
    upsert_holder_remote,
)
from services.git_integration_worker.cursor_dispatch_ledger import CursorDispatchLedger
from services.git_integration_worker.routes.admin import router

pytestmark = pytest.mark.offline

_UUID = "2762063d-2966-4859-a3ce-23831c6e0c86"
_UNIFIED = f"https://claude.ai/chat/{_UUID}"


@pytest.fixture(autouse=True)
def _redirect_background_uds_publishers(
    _events_sink_default_off_live: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Redirect already-spawned UDS publisher daemon threads to the per-test socket."""
    for mod_name in (
        "agent_bus_store.events.publisher",
        "libs.agent_bus_store.events.publisher",
        "cortex_store.event_publisher",
        "libs.cortex_store.event_publisher",
        "mcp_events",
    ):
        mod = sys.modules.get(mod_name)
        pub = getattr(mod, "_publisher", None) if mod is not None else None
        if pub is not None and hasattr(pub, "_sock_path"):
            monkeypatch.setattr(pub, "_sock_path", _events_sink_default_off_live)
    from services.git_integration_worker.tests.events_sink_hermetic import (
        live_connect_attempts,
    )

    live_connect_attempts.clear()


@pytest.fixture
def ledger(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> CursorDispatchLedger:
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "giw-data"))
    monkeypatch.setattr(CursorDispatchLedger, "_instance", None)
    inst = CursorDispatchLedger.instance()
    with inst._connect() as conn:
        ensure_schema(conn)
    return inst


def _isolate_registry(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    from claude_bundles import cdp_registry as reg
    from claude_bundles import cdp_registry_store as store_mod
    from claude_bundles import cse_provenance

    root = tmp_path / "cdp-registry"
    root.mkdir()
    regs = root / "registrations"
    regs.mkdir()
    paths = {
        "REGISTRY_DIR": root,
        "REGISTRY_LOG": root / "registry.jsonl",
        "ACTIVE_JSON": root / "active.json",
        "SESSIONS_JSON": root / "sessions.json",
        "SESSION_TRANSITIONS_JSONL": root / "session_transitions.jsonl",
        "PORTS_LOCK": root / "ports.lock",
        "REGISTRATIONS_DIR": regs,
    }
    monkeypatch.setenv("CDP_REGISTRY_SEAT_AUTHORITY", "1")
    for name, value in paths.items():
        monkeypatch.setattr(reg._store, name, value)
        monkeypatch.setattr(cse_provenance.store, name, value)
        monkeypatch.setattr(store_mod, name, value)
    store_mod.ensure_dirs()
    monkeypatch.setattr(reg._store, "is_seat_authority", lambda: True)
    return root


class _HttpResponseAdapter:
    """Context-manager HTTP response adapter returned by the TestClient urlopen shim."""

    def __init__(self, status: int, body: bytes) -> None:
        self.status = status
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> _HttpResponseAdapter:
        return self

    def __exit__(self, *_exc: object) -> bool:
        return False


def _install_urlopen_admin_adapter(
    monkeypatch: pytest.MonkeyPatch,
) -> list[tuple[int, dict[str, Any]]]:
    """Forward ``cse_session_holders.urllib.request.urlopen`` to real ``admin.router``."""
    app = FastAPI()
    app.include_router(router)
    test_client = TestClient(app, raise_server_exceptions=False)
    observed: list[tuple[int, dict[str, Any]]] = []

    def _urlopen(req: urllib.request.Request, timeout: float = 2.0) -> _HttpResponseAdapter:
        del timeout
        parsed_path = urllib.request.urlsplit(req.full_url).path
        raw_body = req.data or b"{}"
        resp = test_client.request(
            req.get_method(),
            parsed_path,
            content=raw_body,
            headers=dict(req.header_items()),
        )
        try:
            body_json = resp.json()
        except Exception:
            body_json = {"raw": resp.text}
        observed.append((resp.status_code, body_json if isinstance(body_json, dict) else {}))
        if resp.status_code >= 400:
            raise urllib.error.HTTPError(
                url=req.full_url,
                code=resp.status_code,
                msg="HTTP Error",
                hdrs={},  # type: ignore[arg-type]
                fp=BytesIO(resp.content),
            )
        return _HttpResponseAdapter(resp.status_code, resp.content)

    monkeypatch.setattr(cse_session_holders.urllib.request, "urlopen", _urlopen)
    return observed


def test_bind_unified_chat_non_houseless_projects_holder_unmocked(
    ledger: CursorDispatchLedger,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Blank-purpose bind projects one findable chat_<uuid> holder and journals applies (AC13b)."""
    from claude_bundles import cdp_registry_store as store
    from claude_bundles.cdp_registry import session_address

    _isolate_registry(monkeypatch, tmp_path)
    monkeypatch.setenv("ULG_ALLOW_REMOTE_HOLDER_WRITE_UNDER_PYTEST", "1")
    observed = _install_urlopen_admin_adapter(monkeypatch)

    reg = "reg-non-houseless-unified"
    store.write_active(
        {
            reg: {
                "registration_id": reg,
                "port": 9234,
                "status": "active",
                "purpose": "",
                "chat_url": "",
            }
        }
    )

    assert session_address.bind_session_address(reg, chat_url=_UNIFIED) is True
    assert len(observed) == 1
    assert observed[0][0] == 200

    with ledger._connect() as conn:
        rows = conn.execute("SELECT * FROM cse_session_holders").fetchall()
        found = resolve_holder_by_registration(conn, reg)
    assert len(rows) == 1
    assert found is not None
    assert found["holder_id"] == f"chat_{_UUID}"
    assert found["registration_id"] == reg

    journal_rows = [
        json.loads(line)
        for line in store.REGISTRY_LOG.read_text(encoding="utf-8").splitlines()
        if json.loads(line).get("event") == "session_address_bound"
        and json.loads(line).get("registration_id") == reg
    ]
    assert len(journal_rows) == 1
    assert journal_rows[0].get("holder_projection") == "applies"


def test_remote_upsert_refusal_is_typed_unmocked(
    ledger: CursorDispatchLedger,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Houseless upsert_holder_remote receives HTTP 422 houseless_registration -> REFUSED (AC23)."""
    from claude_bundles import cdp_registry_store as store

    _isolate_registry(monkeypatch, tmp_path)
    monkeypatch.setenv("ULG_ALLOW_REMOTE_HOLDER_WRITE_UNDER_PYTEST", "1")
    observed = _install_urlopen_admin_adapter(monkeypatch)

    reg = "reg-houseless-remote-refused"
    store.write_active(
        {
            reg: {
                "registration_id": reg,
                "port": 9235,
                "status": "active",
                "purpose": "houseless",
                "chat_url": _UNIFIED,
            }
        }
    )

    outcome = upsert_holder_remote(chat_url=_UNIFIED, registration_id=reg)
    assert len(observed) == 1
    status_code, body = observed[0]
    assert status_code == 422
    assert isinstance(body.get("detail"), dict)
    assert body["detail"].get("reason") == "houseless_registration"
    assert outcome is RemoteUpsert.REFUSED

    with ledger._connect() as conn:
        count = conn.execute(
            "SELECT COUNT(*) AS n FROM cse_session_holders"
        ).fetchone()["n"]
    assert count == 0


def test_upsert_route_maps_invalid_chat_url_to_422(
    ledger: CursorDispatchLedger,
) -> None:
    """Invalid chat_url on POST /cse-holder/upsert returns HTTP 422 invalid_chat_url."""
    del ledger
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        resp = client.post(
            "/api/v1/git/admin/cse-holder/upsert",
            json={"chat_url": "https://claude.ai/new", "registration_id": "reg-bad-url"},
        )
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert detail["reason"] == "invalid_chat_url"


def test_registry_read_error_is_not_a_refusal(
    ledger: CursorDispatchLedger,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Non-UTF-8 active.json skips the houseless check with a warning and still upserts."""
    from claude_bundles import cdp_registry_store as store

    _isolate_registry(monkeypatch, tmp_path)
    store.ACTIVE_JSON.write_bytes(b"\xff\xfe" + b"reg-corrupt-utf8")

    with caplog.at_level("WARNING"):
        with ledger._connect() as conn:
            row = upsert_holder(
                conn,
                chat_url=_UNIFIED,
                registration_id="reg-corrupt-utf8",
            )
            persisted = get_holder(conn, f"chat_{_UUID}")
    assert row["holder_id"] == f"chat_{_UUID}"
    assert persisted is not None
    assert any(
        "io-local registry houseless check skipped" in rec.getMessage()
        for rec in caplog.records
    )


def test_refusal_emits_signal(
    ledger: CursorDispatchLedger,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Houseless refusal emits cse.holder.upsert_refused via publish_lib_signal."""
    from claude_bundles import cdp_registry_store as store

    from services.git_integration_worker import events as giw_events
    from services.git_integration_worker.cse_session_holders import (
        HolderProjectionRefusedError,
    )

    _isolate_registry(monkeypatch, tmp_path)
    reg = "reg-houseless-signal"
    store.write_active(
        {
            reg: {
                "registration_id": reg,
                "port": 9236,
                "status": "active",
                "purpose": "houseless",
            }
        }
    )
    published: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(
        giw_events,
        "publish_lib_signal",
        lambda sig, payload: published.append((sig, dict(payload))),
    )
    with ledger._connect() as conn:
        with pytest.raises(HolderProjectionRefusedError) as exc_info:
            upsert_holder(conn, chat_url=_UNIFIED, registration_id=reg)
    assert exc_info.value.reason == "houseless_registration"
    assert published == [
        (
            "cse.holder.upsert_refused",
            {
                "holder_id": f"chat_{_UUID}",
                "registration_id": reg,
                "reason": "houseless_registration",
            },
        )
    ]
