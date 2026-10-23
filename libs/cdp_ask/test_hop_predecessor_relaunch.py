"""Hop admit relaunches dormant predecessor before register_lane (friction 40001)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from cdp_ask.models import SubmitProjectAskRequest
from cdp_ask.runner import bind_execution_lane


def test_hop_bind_relaunch_before_register_and_stamps_hold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    call_order: list[str] = []
    pred = "pred-dormant-reg"
    active: dict = {pred: {"registration_id": pred, "status": "dormant"}}

    def _load() -> dict:
        return dict(active)

    def _write(new: dict) -> None:
        active.clear()
        active.update(new)

    fixed_now = 1_700_000_000.0
    monkeypatch.setattr("cdp_ask.runner.time.time", lambda: fixed_now)
    monkeypatch.setattr(
        "claude_bundles.cdp_registry_store.load_active",
        _load,
    )
    monkeypatch.setattr(
        "claude_bundles.cdp_registry_store.write_active",
        _write,
    )

    def _relaunch(registration_id: str, *, holder: str) -> MagicMock:
        call_order.append("relaunch")
        assert registration_id == pred
        return MagicMock(registration_id=registration_id)

    minted = MagicMock(registration_id="hop-row")

    def _register(**_kwargs: object) -> MagicMock:
        call_order.append("register")
        return minted

    req = SubmitProjectAskRequest(
        prompt_text="hop leg",
        holder="hop",
        purpose="operator-proxy",
        mission_kind="hop",
        parent_thread="9497",
        predecessor_registration_id=pred,
    )
    with (
        patch("cdp_ask.runner.cdp_registry.relaunch_dormant", side_effect=_relaunch),
        patch("cdp_ask.runner.cdp_registry.register_lane", side_effect=_register),
        patch(
            "claude_bundles.cdp_registry.session_address.retire_predecessor_identity",
        ),
    ):
        out = bind_execution_lane(req, holder="hop")
    assert out is minted
    assert call_order == ["relaunch", "register"]
    hold = active[pred].get("predecessor_hold_until")
    assert isinstance(hold, float)
    assert hold == pytest.approx(fixed_now + 1800, rel=1.0)


def test_relaunch_dormant_refuses_seat_closed_at(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from claude_bundles import cdp_registry as reg
    from claude_bundles.cdp_registry.models import RegistryError

    root = tmp_path / "cdp-registry"
    regs = root / "registrations"
    regs.mkdir(parents=True)
    monkeypatch.setattr(reg._store, "REGISTRY_DIR", root)
    monkeypatch.setattr(reg._store, "REGISTRY_LOG", root / "registry.jsonl")
    monkeypatch.setattr(reg._store, "ACTIVE_JSON", root / "active.json")
    monkeypatch.setattr(reg._store, "PORTS_LOCK", root / "ports.lock")
    monkeypatch.setattr(reg._store, "REGISTRATIONS_DIR", regs)
    rid = "closed-seat-reg"
    reg._store.write_active(
        {
            rid: {
                "registration_id": rid,
                "status": "dormant",
                "seat_closed_at": 99.0,
                "profile_suffix": "closed",
            }
        }
    )
    with pytest.raises(RegistryError, match="seat_closed_at"):
        reg.relaunch_dormant(rid, holder="test")
