"""Satellite ``/v1/cse-session/bind-address`` and ``retire-binding`` route tests."""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.offline

_UUID = "2762063d-2966-4859-a3ce-23831c6e0c86"
_UNIFIED = f"https://claude.ai/chat/{_UUID}"
_OTHER = "https://claude.ai/cowork/cse_other_bound"


@pytest.fixture
def isolated_registry(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    from claude_bundles import cdp_registry as reg
    from claude_bundles import cdp_registry_store as store_mod
    from claude_bundles import cse_provenance

    from services.git_integration_worker import cse_session_holders

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
    monkeypatch.setattr(reg._store, "is_seat_authority", lambda: True)
    monkeypatch.setattr(
        cse_session_holders,
        "upsert_holder_remote",
        lambda **_kw: cse_session_holders.RemoteUpsert.OK,
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
    purpose: str | None = None,
    execution_state: dict | None = None,
) -> None:
    from claude_bundles import cdp_registry_store as store

    row: dict = {
        "registration_id": registration_id,
        "port": 9223,
        "status": "active",
        "chat_url": chat_url,
    }
    if purpose is not None:
        row["purpose"] = purpose
    if execution_state is not None:
        row["execution_state"] = execution_state
    store.write_active({registration_id: row})


def _bound_journal_rows(registration_id: str) -> list[dict]:
    from claude_bundles import cdp_registry_store as store

    if not store.REGISTRY_LOG.exists():
        return []
    out: list[dict] = []
    for line in store.REGISTRY_LOG.read_text(encoding="utf-8").splitlines():
        rec = json.loads(line)
        if (
            rec.get("event") == "session_address_bound"
            and rec.get("registration_id") == registration_id
        ):
            out.append(rec)
    return out


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


def test_retire_binding_default_dry_run_preview(client: TestClient) -> None:
    rid = "reg-retire-default"
    from claude_bundles import cdp_registry_store as store

    store.write_active(
        {
            rid: {
                "registration_id": rid,
                "port": 9223,
                "status": "dormant",
                "chat_url": _UNIFIED,
            }
        }
    )
    resp = client.post(
        "/v1/cse-session/retire-binding",
        json={"registration_id": rid, "chat_url": _UNIFIED},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body.get("dry_run") is True
    assert store.load_active()[rid]["chat_url"] == _UNIFIED


def test_repair_bind_journals_provenance(client: TestClient) -> None:
    rid = "reg-repair-journal"
    _write_row(rid)
    payload = {
        "registration_id": rid,
        "chat_url": _UNIFIED,
        "dry_run": False,
        "reason": "repair_bind_address",
    }
    first = client.post("/v1/cse-session/bind-address", json=payload)
    assert first.status_code == 200, first.text
    second = client.post("/v1/cse-session/bind-address", json=payload)
    assert second.status_code == 200, second.text

    rows = _bound_journal_rows(rid)
    assert len(rows) == 2
    for rec in rows:
        assert rec.get("bound_by") == "cse_session.bind_address"
        assert rec.get("attach_proof") == "repair_harvest_url"


def test_in_flight_refuses_even_when_bound(client: TestClient) -> None:
    rid = "reg-in-flight-prior"
    _write_row(
        rid,
        chat_url=_UNIFIED,
        execution_state={
            "state": "streaming",
            "started_at": time.time(),
            "execution_id": "exec-bound-in-flight",
        },
    )
    for dry_run in (True, False):
        resp = client.post(
            "/v1/cse-session/bind-address",
            json={"registration_id": rid, "chat_url": _UNIFIED, "dry_run": dry_run},
        )
        assert resp.status_code == 409
        assert resp.json().get("code") == "in_flight"
    assert _bound_journal_rows(rid) == []


def test_stamp_outcome_reported(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from agent_bus_store.db import create_thread, get_turns, init_db
    from agent_bus_store.db.connection import write_connect
    from claude_bundles import cdp_registry_store as store

    bus_db = tmp_path / "bus.db"
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(bus_db))
    init_db()
    thread_posted = create_thread(thread_id=None, slug="stamp-posted-lane")
    thread_failed = create_thread(thread_id=None, slug="stamp-failed-lane")
    assert thread_posted is not None and thread_failed is not None
    lane_posted = str(thread_posted["id"])
    lane_failed = str(thread_failed["id"])

    with patch("agent_bus_store.db.cse_associations.emit_cse_bound"):
        rid_posted = "reg-stamp-posted"
        _write_row(rid_posted)
        resp_posted = client.post(
            "/v1/cse-session/bind-address",
            json={
                "registration_id": rid_posted,
                "chat_url": _UNIFIED,
                "dry_run": False,
                "parent_thread": lane_posted,
            },
        )
        assert resp_posted.status_code == 200, resp_posted.text
        assert resp_posted.json().get("stamp") == "posted"
        turns = get_turns(thread=lane_posted, last=5)
        stamps = [
            t
            for t in turns
            if str(t.get("body") or "").startswith("TYPE: SEAT_REGISTRATION")
        ]
        assert len(stamps) == 1
        assert _UNIFIED in str(stamps[0]["body"])

        rid_skip = "reg-stamp-skip-parent"
        _write_row(rid_skip)
        resp_skip = client.post(
            "/v1/cse-session/bind-address",
            json={
                "registration_id": rid_skip,
                "chat_url": "https://claude.ai/cowork/cse_stamp_skip",
                "dry_run": False,
            },
        )
        assert resp_skip.status_code == 200, resp_skip.text
        assert resp_skip.json().get("stamp") == "skipped_no_parent_thread"

        with write_connect() as conn:
            conn.execute(
                "CREATE TRIGGER fail_stamp_turn BEFORE INSERT ON turns "
                "BEGIN SELECT RAISE(ABORT, 'bus turns unavailable'); END;"
            )
        rid_fail = "reg-stamp-failed"
        fail_url = "https://claude.ai/cowork/cse_stamp_failed"
        _write_row(rid_fail)
        resp_fail = client.post(
            "/v1/cse-session/bind-address",
            json={
                "registration_id": rid_fail,
                "chat_url": fail_url,
                "dry_run": False,
                "parent_thread": lane_failed,
            },
        )
        assert resp_fail.status_code == 200, resp_fail.text
        body_fail = resp_fail.json()
        assert body_fail.get("ok") is True
        assert body_fail.get("stamp") == "failed"
        assert body_fail.get("stamp_error") == "IntegrityError"
        assert store.load_active()[rid_fail]["chat_url"] == fail_url


def test_repair_bind_houseless_makes_no_holder_projection(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from agent_bus_store.db import create_thread, init_db

    from services.git_integration_worker import cse_session_holders

    bus_db = tmp_path / "bus-hl.db"
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(bus_db))
    init_db()
    detail = create_thread(thread_id=None, slug="repair-hl-mailbox", tags=["mailbox"])
    assert detail is not None
    lane_id = str(detail["id"])

    holder_calls: list[dict] = []

    def _counting_upsert(**kwargs: object) -> cse_session_holders.RemoteUpsert:
        holder_calls.append(dict(kwargs))
        return cse_session_holders.RemoteUpsert.OK

    monkeypatch.setattr(
        cse_session_holders, "upsert_holder_remote", _counting_upsert
    )

    rid = "reg-repair-houseless"
    _write_row(rid, purpose="houseless")
    with patch("agent_bus_store.db.cse_associations.emit_cse_bound"):
        resp = client.post(
            "/v1/cse-session/bind-address",
            json={
                "registration_id": rid,
                "chat_url": _UNIFIED,
                "dry_run": False,
                "parent_thread": lane_id,
            },
        )
    assert resp.status_code == 200, resp.text
    assert resp.json().get("ok") is True
    assert holder_calls == []
    rows = _bound_journal_rows(rid)
    assert len(rows) == 1
    assert rows[0].get("holder_projection") == "not_applicable"


def test_project_holder_non_enum_return_never_raises(
    monkeypatch: pytest.MonkeyPatch,
    isolated_registry: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """D18: stale remote upsert shapes must not raise from bind or _project_holder."""
    import logging

    from claude_bundles import cdp_registry as reg
    from claude_bundles.cdp_registry.session_address import _project_holder

    from services.git_integration_worker import cse_session_holders

    caplog.set_level(logging.WARNING, logger="claude_bundles.cdp_registry.session_address")

    stale_return: dict[str, object] = {"ok": True, "skipped": "pytest"}
    calls: list[dict[str, object]] = []

    def _stale_upsert(**kwargs: object) -> dict[str, object]:
        calls.append(dict(kwargs))
        return stale_return

    monkeypatch.setattr(
        cse_session_holders, "upsert_holder_remote", _stale_upsert
    )

    seat = reg.register_lane(
        holder="d18-non-enum",
        purpose="operator-proxy",
        mission_kind="root",
        parent_thread="88901",
        launch_chrome=lambda port, profile: 1,
        is_listening=lambda _p: False,
    )
    rid = seat.registration_id

    assert reg.bind_session_address(rid, chat_url=_UNIFIED) is True
    assert len(calls) == 1

    bind_warnings = [
        r
        for r in caplog.records
        if r.levelname == "WARNING"
        and rid in r.getMessage()
        and "dict" in r.getMessage()
    ]
    assert len(bind_warnings) == 1

    row = reg._store.load_active()[rid]
    assert (
        _project_holder(
            rid,
            row=row,
            chat_url=_UNIFIED,
            execution_id=None,
        )
        == cse_session_holders.RemoteUpsert.UNAVAILABLE.value
    )
