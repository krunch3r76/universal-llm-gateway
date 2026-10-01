"""reattach() on dormant rows — same holder relaunches, mismatch errors closed."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from claude_bundles import cdp_registry as reg

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


def _seat(chat_url: str) -> Any:
    reg_row = reg.register_lane(
        holder="reattach-dormant-test",
        purpose="operator-proxy",
        launch_chrome=_noop_launch,
        is_listening=lambda _p: False,
    )
    reg.bind_session_address(reg_row.registration_id, chat_url=chat_url)
    reg._release_driver_lock(reg_row.registration_id)
    return reg_row


def _row(registration_id: str) -> dict[str, Any]:
    return reg._load_active()[registration_id]


def test_reattach_dormant_same_holder_relaunches_active(
    isolated_registry: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    url = "https://claude.ai/cowork/cse_reattach_wake"
    seat = _seat(chat_url=url)
    assert reg.make_dormant(seat.registration_id, is_listening=lambda _p: False)
    monkeypatch.setattr(reg.cdp_lane, "_launch_chrome", _noop_launch)

    again = reg.reattach(seat.registration_id, holder="reattach-dormant-test")
    assert again.registration_id == seat.registration_id
    row = _row(seat.registration_id)
    assert row["status"] == "active"
    assert row["chat_url"] == url


def test_reattach_dormant_holder_mismatch_does_not_relaunch(
    isolated_registry: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    url = "https://claude.ai/cowork/cse_reattach_mismatch"
    seat = _seat(chat_url=url)
    assert reg.make_dormant(seat.registration_id, is_listening=lambda _p: False)

    launches: list[int] = []

    def _tracked_launch(port: int, profile: Path) -> int:
        launches.append(port)
        return _noop_launch(port, profile)

    monkeypatch.setattr(reg.cdp_lane, "_launch_chrome", _tracked_launch)

    with pytest.raises(reg.RegistryError, match="holder mismatch"):
        reg.reattach(seat.registration_id, holder="foreign-holder")

    assert _row(seat.registration_id)["status"] == "dormant"
    assert launches == []
