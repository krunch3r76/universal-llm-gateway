"""Detach skips Chrome kill and tape scrape when another row holds the same port (a:40126)."""

from __future__ import annotations

from pathlib import Path

import pytest
from claude_bundles import cdp_registry as reg
from fastapi.testclient import TestClient

pytestmark = pytest.mark.offline

_LANE = "19912"
_CSE_PRED = "https://claude.ai/cowork/cse_port_shared_pred"
_CSE_SUCC = "https://claude.ai/cowork/cse_port_shared_succ"
_SHARED_PORT = 9223


@pytest.fixture
def isolated_registry(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    root = tmp_path / "cdp-registry"
    root.mkdir()
    regs = root / "registrations"
    regs.mkdir()
    monkeypatch.setenv("CDP_REGISTRY_SEAT_AUTHORITY", "1")
    monkeypatch.setattr(reg._store, "REGISTRY_DIR", root)
    monkeypatch.setattr(reg._store, "REGISTRY_LOG", root / "registry.jsonl")
    monkeypatch.setattr(reg._store, "ACTIVE_JSON", root / "active.json")
    monkeypatch.setattr(reg._store, "PORTS_LOCK", root / "ports.lock")
    monkeypatch.setattr(reg._store, "REGISTRATIONS_DIR", regs)
    monkeypatch.setattr(reg, "REGISTRY_DIR", root)
    monkeypatch.setattr(reg, "REGISTRY_LOG", root / "registry.jsonl")
    monkeypatch.setattr(reg, "ACTIVE_JSON", root / "active.json")
    monkeypatch.setattr(reg, "PORTS_LOCK", root / "ports.lock")
    monkeypatch.setattr(reg, "REGISTRATIONS_DIR", regs)
    monkeypatch.setattr(reg, "_HELD_LOCKS", {})
    profiles = tmp_path / "profiles"
    profiles.mkdir()
    monkeypatch.setattr(
        reg.cdp_lane,
        "profile_for",
        lambda suffix: profiles / f"claude-ai-chrome-profile-{suffix}",
    )
    return root


def _detach_client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    from cdp_ask.app import create_app

    return TestClient(create_app())


def test_detach_shared_port_skips_kill_and_tape_scrape(
    isolated_registry: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two rows on one port: dormant detach must not kill the shared listener."""
    killed: list[int] = []
    monkeypatch.setattr(reg, "_kill_listener", lambda port: killed.append(port))
    monkeypatch.setattr("claude_bundles.cdp_lane.is_listening", lambda _p: True)
    monkeypatch.setattr(
        "claude_bundles.cdp_registry.hygiene.reclaim_profile_for_detached_row",
        lambda *_a, **_k: "success",
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cse_session_holders.release_holder_remote",
        lambda **_k: True,
    )

    async def _forbidden_scrape(_port: int, _url: str) -> list:
        raise AssertionError("scrape must not run when port is shared")

    monkeypatch.setattr(
        "cdp_ask.cse_session_tape_seal._scrape_predecessor_turns",
        _forbidden_scrape,
    )

    pred_rid = "rid-pred-shared-port"
    succ_rid = "rid-succ-shared-port"
    reg._store.write_active(
        {
            pred_rid: {
                "registration_id": pred_rid,
                "port": _SHARED_PORT,
                "profile_suffix": "reg-pred",
                "holder": "pred",
                "status": "dormant",
                "chat_url": _CSE_PRED,
                "parent_thread": _LANE,
                "seat_lane": _LANE,
                "seat_closed_at": 1.0,
                "superseded_by": succ_rid,
            },
            succ_rid: {
                "registration_id": succ_rid,
                "port": _SHARED_PORT,
                "profile_suffix": "reg-succ",
                "holder": "succ",
                "status": "active",
                "chat_url": _CSE_SUCC,
                "parent_thread": _LANE,
                "seat_lane": _LANE,
                "seat_closed_at": None,
            },
        }
    )
    reg._store.append_log(
        "standdown_pasted",
        {"registration_id": pred_rid, "chat_url": _CSE_PRED, "pasted_at": 1.0},
    )

    client = _detach_client(monkeypatch)
    response = client.post(
        "/v1/cse-session/detach",
        json={"registration_id": pred_rid, "reason": "hop_detach"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body.get("tape_seal") == "skipped_port_shared"
    assert body.get("listener_killed") is False
    assert killed == []
    assert pred_rid not in reg._load_active()
    assert succ_rid in reg._load_active()


def test_detach_sole_row_kills_listener(
    isolated_registry: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One row on a listening port: detach kills the listener."""
    killed: list[int] = []
    monkeypatch.setattr(reg, "_kill_listener", lambda port: killed.append(port))
    monkeypatch.setattr("claude_bundles.cdp_lane.is_listening", lambda _p: True)
    monkeypatch.setattr(
        "claude_bundles.cdp_registry.hygiene.reclaim_profile_for_detached_row",
        lambda *_a, **_k: "success",
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cse_session_holders.release_holder_remote",
        lambda **_k: True,
    )

    rid = "rid-sole-port"
    reg._store.write_active(
        {
            rid: {
                "registration_id": rid,
                "port": _SHARED_PORT,
                "profile_suffix": "reg-sole",
                "holder": "sole",
                "status": "dormant",
                "chat_url": _CSE_PRED,
                "parent_thread": _LANE,
                "seat_lane": _LANE,
                "seat_closed_at": 1.0,
            },
        }
    )

    result = reg.detach(rid, reason="hop_done")
    assert result.get("listener_killed") is True
    assert killed == [_SHARED_PORT]
    assert rid not in reg._load_active()
