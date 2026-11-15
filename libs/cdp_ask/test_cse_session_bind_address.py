"""Satellite ``/v1/cse-session/bind-address`` route tests (AC9b, D10)."""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.offline

_UUID = "2762063d-2966-4859-a3ce-23831c6e0c86"
_UNIFIED = f"https://claude.ai/chat/{_UUID}"
_OTHER = "https://claude.ai/cowork/cse_other_bound"


@pytest.fixture
def isolated_registry(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    from claude_bundles import cdp_registry as reg
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
    monkeypatch.setattr(reg._store, "is_seat_authority", lambda: True)
    from services.git_integration_worker.cse_session_holders import RemoteUpsert

    monkeypatch.setattr(
        "services.git_integration_worker.cse_session_holders.upsert_holder_remote",
        lambda **_kw: RemoteUpsert.OK,
    )
    return root


@pytest.fixture
def client(
    monkeypatch: pytest.MonkeyPatch, isolated_registry: Path, tmp_path: Path
) -> TestClient:
    monkeypatch.setattr("cdp_ask.app.verify_harvest_root", lambda: tmp_path)
    from cdp_ask.app import create_app

    return TestClient(create_app())


def _write_row(
    registration_id: str,
    *,
    chat_url: str = "",
    execution_state: dict | None = None,
) -> None:
    from claude_bundles import cdp_registry_store as store

    row: dict = {
        "registration_id": registration_id,
        "port": 9223,
        "status": "active",
        "chat_url": chat_url,
    }
    if execution_state is not None:
        row["execution_state"] = execution_state
    store.write_active({registration_id: row})


def test_bind_address_default_dry_run_no_write(client: TestClient) -> None:
    rid = "reg-dry-default"
    _write_row(rid)
    resp = client.post(
        "/v1/cse-session/bind-address",
        json={"registration_id": rid, "chat_url": _UNIFIED},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body.get("dry_run") is True
    assert body.get("would_write") is True
    from claude_bundles import cdp_registry_store as store

    assert store.load_active()[rid].get("chat_url") == ""


def test_bind_address_live_bind_and_idempotent(client: TestClient) -> None:
    rid = "reg-live-bind"
    _write_row(rid)
    first = client.post(
        "/v1/cse-session/bind-address",
        json={"registration_id": rid, "chat_url": _UNIFIED, "dry_run": False},
    )
    assert first.status_code == 200, first.text
    assert first.json().get("bound") is True
    from claude_bundles import cdp_registry_store as store

    assert store.load_active()[rid]["chat_url"] == _UNIFIED
    second = client.post(
        "/v1/cse-session/bind-address",
        json={"registration_id": rid, "chat_url": _UNIFIED, "dry_run": False},
    )
    assert second.status_code == 200
    assert second.json().get("idempotent") is True


def test_bind_address_junk_url_422(client: TestClient) -> None:
    rid = "reg-junk"
    _write_row(rid)
    resp = client.post(
        "/v1/cse-session/bind-address",
        json={"registration_id": rid, "chat_url": "https://claude.ai/junk"},
    )
    assert resp.status_code == 422
    assert resp.json().get("code") == "not_session_address"


def test_bind_address_already_bound_409(client: TestClient) -> None:
    rid = "reg-already"
    _write_row(rid, chat_url=_OTHER)
    resp = client.post(
        "/v1/cse-session/bind-address",
        json={"registration_id": rid, "chat_url": _UNIFIED, "dry_run": False},
    )
    assert resp.status_code == 409
    assert resp.json().get("code") == "already_bound"


def test_bind_address_in_flight_409(client: TestClient) -> None:
    rid = "reg-in-flight"
    _write_row(
        rid,
        execution_state={
            "state": "streaming",
            "started_at": time.time(),
            "execution_id": "exec-in-flight-bind",
        },
    )
    resp = client.post(
        "/v1/cse-session/bind-address",
        json={"registration_id": rid, "chat_url": _UNIFIED, "dry_run": False},
    )
    assert resp.status_code == 409
    assert resp.json().get("code") == "in_flight"


def test_bind_address_missing_row(client: TestClient) -> None:
    resp = client.post(
        "/v1/cse-session/bind-address",
        json={"registration_id": "missing-reg", "chat_url": _UNIFIED},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("code") == "missing_row"
    assert body.get("ok") is False
