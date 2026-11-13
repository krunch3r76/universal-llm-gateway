"""``/v1/project-ask/reattach-house`` wakes dormant submit-holder lanes (a:43814)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from claude_bundles import cdp_registry as reg
from fastapi.testclient import TestClient

pytestmark = pytest.mark.offline


@pytest.fixture
def isolated_registry(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    root = tmp_path / "cdp-registry"
    root.mkdir()
    regs = root / "registrations"
    regs.mkdir()
    for name, value in {
        "REGISTRY_DIR": root,
        "REGISTRY_LOG": root / "registry.jsonl",
        "ACTIVE_JSON": root / "active.json",
        "SESSIONS_JSON": root / "sessions.json",
        "SESSION_TRANSITIONS_JSONL": root / "session_transitions.jsonl",
        "PORTS_LOCK": root / "ports.lock",
        "REGISTRATIONS_DIR": regs,
    }.items():
        monkeypatch.setattr(reg._store, name, value)
    monkeypatch.setattr(reg, "_HELD_LOCKS", {})
    monkeypatch.setattr(reg, "PORT_RANGE", range(9223, 9228))
    profiles = tmp_path / "profiles"
    profiles.mkdir()
    monkeypatch.setattr(
        reg.cdp_lane,
        "profile_for",
        lambda suffix: profiles / f"claude-ai-chrome-profile-{suffix}",
    )
    monkeypatch.setattr(reg.cdp_lane, "PRIMARY_PROFILE", profiles / "primary")
    (profiles / "primary").mkdir()
    return root


def _noop_launch(port: int, profile: Path) -> int:
    profile.mkdir(parents=True, exist_ok=True)
    return 4242


def _dormant_submit_holder(purpose: str = "ask") -> Any:
    row = reg.register_lane(
        holder="cdp-model-endpoint",
        purpose=purpose,
        launch_chrome=_noop_launch,
        is_listening=lambda _p: False,
    )
    reg.bind_session_address(
        row.registration_id,
        chat_url="https://claude.ai/cowork/cse_pre_paste",
    )
    reg._release_driver_lock(row.registration_id)
    assert reg.make_dormant(row.registration_id, is_listening=lambda _p: False)
    return row


def test_reattach_house_wakes_dormant_submit_holder(
    isolated_registry: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seat = _dormant_submit_holder()
    monkeypatch.setattr(reg.cdp_lane, "_launch_chrome", _noop_launch)

    from cdp_ask.app import create_app

    client = TestClient(create_app())
    resp = client.post("/v1/project-ask/reattach-house", json={"purpose": "ask"})
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("ok") is True
    assert body.get("reattached") is True
    assert body.get("registration_id") == seat.registration_id
    active = reg._load_active()[seat.registration_id]
    assert active["status"] == "active"


def test_reattach_house_registry_error_refuse_envelope(
    isolated_registry: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _dormant_submit_holder()

    def _raise(*_a: object, **_k: object) -> None:
        raise reg.RegistryError("holder mismatch for test")

    monkeypatch.setattr(reg, "reattach", _raise)

    from cdp_ask.app import create_app

    client = TestClient(create_app())
    resp = client.post("/v1/project-ask/reattach-house", json={"purpose": "ask"})
    assert resp.status_code == 409
    body = resp.json()
    assert body.get("code") == "reattach_refused"
    assert "message" in body
    assert body.get("source") == "gateway"
