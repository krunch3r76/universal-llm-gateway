"""Phase 1 falsifiers for l2 r6 (uncommitted until paired fix commits)."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cdp_ask.test_cse_session_bind_address import _write_row

pytestmark = pytest.mark.offline


@pytest.fixture
def l2r6_isolated_registry(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    from claude_bundles import cdp_registry as reg
    from claude_bundles import cdp_registry_store as store_mod
    from claude_bundles import cse_provenance
    from services.git_integration_worker import cse_session_holders

    root = tmp_path / "cdp-registry-l2r6"
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
def l2r6_client(
    monkeypatch: pytest.MonkeyPatch, l2r6_isolated_registry: Path, tmp_path: Path
) -> TestClient:
    monkeypatch.setattr("cdp_ask.app.verify_harvest_root", lambda: tmp_path)
    from cdp_ask.app import create_app

    return TestClient(create_app())

_CSE_TOKEN = "cse_l2r6f1"
_LEGACY_WWW = f"https://www.claude.ai/cowork/{_CSE_TOKEN}"
_CANON_COWORK = f"https://claude.ai/cowork/{_CSE_TOKEN}"

_CHAT_UUID = "2762063d-2966-4859-a3ce-23831c6e0c86"
_CANON_CHAT = f"https://claude.ai/chat/{_CHAT_UUID}"
_WWW_UPPER_CHAT = f"https://www.claude.ai/chat/{_CHAT_UUID.upper()}"


def test_f1_i_legacy_www_same_url_idempotent_rewrite(l2r6_client: TestClient) -> None:
    rid = "reg-f1-i"
    _write_row(rid, chat_url=_LEGACY_WWW)
    resp = l2r6_client.post(
        "/v1/cse-session/bind-address",
        json={"registration_id": rid, "chat_url": _LEGACY_WWW, "dry_run": False},
    )
    body = resp.json()
    assert resp.status_code == 200, resp.text
    assert body.get("ok") is True
    assert body.get("idempotent") is True
    from claude_bundles import cdp_registry_store as store

    assert store.load_active()[rid]["chat_url"] == _CANON_COWORK


def test_f1_ii_canonical_row_www_request_idempotent(l2r6_client: TestClient) -> None:
    rid = "reg-f1-ii"
    _write_row(rid, chat_url=_CANON_COWORK)
    resp = l2r6_client.post(
        "/v1/cse-session/bind-address",
        json={"registration_id": rid, "chat_url": _LEGACY_WWW, "dry_run": False},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body.get("ok") is True
    assert body.get("idempotent") is True


@pytest.mark.asyncio
async def test_f2_www_upper_chat_no_identity_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from unittest.mock import AsyncMock

    from cdp_ask.followup import FollowupProjectAskRequest
    from cdp_ask.followup_resolve import resolve_followup_target
    from cdp_ask.test_followup_resolve import store

    monkeypatch.setattr(
        "cdp_ask.followup_resolve.cdp_registry.chat_url_for_registration",
        lambda rid: _CANON_CHAT if rid == "reg-f2" else None,
    )
    scan = AsyncMock(return_value=[])
    monkeypatch.setattr("cdp_ask.followup_resolve.scan_lane_cse_urls", scan)

    req = FollowupProjectAskRequest(
        chat_url=_WWW_UPPER_CHAT,
        registration_id="reg-f2",
        message="hi",
        lane="l",
    )
    _target, err, _path, _binding = await resolve_followup_target(req, store)
    assert err is None or err.error != "identity_mismatch", getattr(err, "error", err)
