"""Attachment hop binds — U-scan, L-fold, D-token, 4A seat (AC1–AC14 subset)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from universal_protocol.errors import ProtocolError

from claude_bundles import cdp_registry as reg
from claude_bundles.cdp_registry.session_address import attachment_for_chat_url
from claude_bundles.cdp_registry_store import (
    fold_attachment_journal,
    open_seats_per_lane,
    verify_seat_fold_invariant,
)

pytestmark = pytest.mark.offline

_LANE = "12564"
_CSE = "https://claude.ai/cowork/cse_attachment_hop_test"
_CSE_ALT = _CSE + "/"


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
    monkeypatch.setattr(reg, "PORT_RANGE", range(9223, 9230))
    profiles = tmp_path / "profiles"
    profiles.mkdir()
    monkeypatch.setattr(
        reg.cdp_lane,
        "profile_for",
        lambda suffix: profiles / f"claude-ai-chrome-profile-{suffix}",
    )
    return root


def _noop_launch(port: int, profile: Path) -> int:
    profile.mkdir(parents=True, exist_ok=True)
    return 1


def _mint(*, holder: str = "h", kind: str = "root") -> Any:
    return reg.register_lane(
        holder=holder,
        purpose="operator-proxy",
        mission_kind=kind,
        parent_thread=_LANE,
        launch_chrome=_noop_launch,
        is_listening=lambda _p: False,
    )


def test_ac1_attachment_conflict(isolated_registry: Path) -> None:
    first = _mint(holder="a")
    second = _mint(holder="b")
    assert reg.bind_session_address(first.registration_id, chat_url=_CSE)
    with pytest.raises(ProtocolError) as exc:
        reg.bind_session_address(second.registration_id, chat_url=_CSE)
    assert exc.value.code == "attachment.conflict"
    assert second.registration_id in reg._load_active()


def test_ac1_streaming_latch_conflict(isolated_registry: Path) -> None:
    from cdp_ask.runner import _latch_streaming_attachment

    a = _mint(holder="a")
    b = _mint(holder="b")
    a_id = a.registration_id
    b_id = b.registration_id
    assert reg.bind_session_address(a_id, chat_url=_CSE)
    before_log = (
        (isolated_registry / "registry.jsonl").read_text()
        if (isolated_registry / "registry.jsonl").exists()
        else ""
    )
    with pytest.raises(ProtocolError) as exc:
        _latch_streaming_attachment(b_id, _CSE, execution_id="eb")
    assert exc.value.code == "attachment.conflict"
    b_row = reg._load_active()[b_id]
    assert not b_row.get("chat_url")
    assert not b_row.get("attached_at")
    after_log = (
        (isolated_registry / "registry.jsonl").read_text()
        if (isolated_registry / "registry.jsonl").exists()
        else ""
    )
    assert after_log == before_log


def test_ac2_normalization_conflict(isolated_registry: Path) -> None:
    first = _mint()
    second = _mint(holder="b")
    assert reg.bind_session_address(first.registration_id, chat_url=_CSE)
    with pytest.raises(ProtocolError):
        reg.bind_session_address(second.registration_id, chat_url=_CSE_ALT)


def test_ac3_ac4_streaming_latch(
    isolated_registry: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cdp_ask import runner

    row = _mint()
    rid = row.registration_id

    hook = runner._wrap_harvest_with_address(
        None, registration_id=rid, execution_id="e1"
    )

    async def _run(state: dict[str, Any]) -> None:
        await hook(state)

    import asyncio

    asyncio.get_event_loop().run_until_complete(_run({"url": _CSE, "streaming": False}))
    assert (
        not (isolated_registry / "registry.jsonl").exists()
        or "attachment_observed"
        not in (isolated_registry / "registry.jsonl").read_text()
    )

    asyncio.get_event_loop().run_until_complete(_run({"url": _CSE, "streaming": True}))
    log = (isolated_registry / "registry.jsonl").read_text()
    assert log.count("attachment_observed") == 1
    active = reg._load_active()[rid]
    assert active.get("attached_at")

    asyncio.get_event_loop().run_until_complete(_run({"url": _CSE, "streaming": True}))
    assert (isolated_registry / "registry.jsonl").read_text().count(
        "attachment_observed"
    ) == 1


def test_ac5_observation_survives_non_authority(
    isolated_registry: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cdp_ask import runner

    monkeypatch.setenv("CDP_REGISTRY_SEAT_AUTHORITY", "0")
    row = _mint()
    rid = row.registration_id
    hook = runner._wrap_harvest_with_address(
        None, registration_id=rid, execution_id="e1"
    )

    async def _run(state: dict[str, Any]) -> None:
        await hook(state)

    import asyncio

    asyncio.get_event_loop().run_until_complete(_run({"url": _CSE, "streaming": True}))
    active = reg._load_active()[rid]
    assert active.get("chat_url")
    assert not active.get("attached_at")

    monkeypatch.setenv("CDP_REGISTRY_SEAT_AUTHORITY", "1")
    fold_attachment_journal()
    active = reg._load_active()[rid]
    assert active.get("attached_at")


def test_fold_replay_skips_u_scan_on_historical_attachment_observed(
    isolated_registry: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Released row's journal line must not conflict with today's live bind (G6 withhold)."""
    from cdp_ask.runner import _latch_streaming_attachment

    monkeypatch.setattr(reg, "_kill_listener", lambda _p: None)
    monkeypatch.setattr(
        "services.git_integration_worker.cse_session_holders.upsert_holder_remote",
        lambda **_k: None,
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cse_session_holders.release_holder_remote",
        lambda **_k: True,
    )

    x = _mint(holder="x")
    x_id = x.registration_id
    _latch_streaming_attachment(x_id, _CSE, execution_id="ex")
    assert reg._load_active()[x_id].get("attached_at")

    reg.deregister_lane(x_id, kill=True, reason="released")

    y = _mint(holder="y")
    y_id = y.registration_id
    assert reg.bind_session_address(y_id, chat_url=_CSE)
    _latch_streaming_attachment(y_id, _CSE, execution_id="ey")
    y_row = reg._load_active()[y_id]
    assert y_row.get("attached_at")

    z = _mint(holder="z")
    z_id = z.registration_id
    other = _CSE + "2"
    assert reg.bind_session_address(z_id, chat_url=other)
    _latch_streaming_attachment(z_id, other, execution_id="ez")
    assert reg._load_active()[z_id].get("attached_at")


def test_fold_replay_session_address_bound_wins_over_earlier_observation(
    isolated_registry: Path,
) -> None:
    """G5r: later session_address_bound must not lose chat_url to earlier observation."""
    row = _mint()
    rid = row.registration_id
    url_observed = _CSE
    url_bound = _CSE + "2"
    reg._store.append_log(
        "attachment_observed",
        {
            "registration_id": rid,
            "chat_url": url_observed,
            "attach_proof": "streaming",
            "observed_at": 1.0,
        },
    )
    reg._store.append_log(
        "session_address_bound",
        {
            "registration_id": rid,
            "chat_url": url_bound,
            "execution_id": "exec-bound",
        },
    )
    fold_attachment_journal()
    replayed = reg._load_active()[rid]
    assert replayed.get("chat_url") == url_bound


def test_fold_replay_observation_only_retains_chat_url(isolated_registry: Path) -> None:
    row = _mint()
    rid = row.registration_id
    reg._store.append_log(
        "attachment_observed",
        {
            "registration_id": rid,
            "chat_url": _CSE,
            "attach_proof": "streaming",
            "observed_at": 1.0,
        },
    )
    fold_attachment_journal()
    assert reg._load_active()[rid].get("chat_url") == _CSE


def test_ac14_attachment_fold_replay(isolated_registry: Path) -> None:
    row = _mint()
    rid = row.registration_id
    reg._store.append_attachment_journal(
        registration_id=rid,
        chat_url=_CSE,
        attach_proof="streaming",
    )
    active = reg._load_active()
    for r in active.values():
        r.pop("attached_at", None)
        r.pop("attach_proof", None)
    reg._store.write_active(active)
    fold_attachment_journal(active)
    replayed = reg._load_active()[rid]
    assert replayed.get("attached_at")
    assert replayed.get("attach_proof") == "streaming"


def test_ac10_hop_successor_seat(isolated_registry: Path) -> None:
    pred = _mint(holder="pred", kind="root")
    assert reg.bind_session_address(pred.registration_id, chat_url=_CSE)
    succ = _mint(holder="succ", kind="hop")
    assert reg.bind_session_address(succ.registration_id, chat_url=_CSE + "2")
    active = reg._load_active()
    assert open_seats_per_lane(active)[_LANE] == [succ.registration_id]
    assert active[pred.registration_id]["superseded_by"] == succ.registration_id
    assert active[pred.registration_id]["seat_closed_at"] is not None
    verify_seat_fold_invariant()


def test_ac11_ensure_hop_entry_refused(isolated_registry: Path) -> None:
    from claude_bundles.cdp_registry.models import RegistryError

    with pytest.raises(RegistryError, match="mission_kind=hop"):
        reg.ensure_driving_operator_seat(
            holder="x", parent_thread=_LANE, mission_kind="hop"
        )


def test_ac6_standdown_missing(isolated_registry: Path) -> None:
    row = _mint()
    rid = row.registration_id
    active = reg._load_active()
    active[rid]["superseded_by"] = "other"
    active[rid]["seat_closed_at"] = 1.0
    reg._store.write_active(active)
    before = (isolated_registry / "active.json").read_bytes()
    with pytest.raises(ProtocolError) as exc:
        reg.detach(rid, reason="test")
    assert exc.value.code == "standdown.missing"
    assert (isolated_registry / "active.json").read_bytes() == before


def test_ac8_detach_pop(
    isolated_registry: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(reg, "_kill_listener", lambda _p: None)
    monkeypatch.setattr(
        "claude_bundles.cdp_registry.hygiene.reclaim_profile_for_detached_row",
        lambda *_a, **_k: "success",
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cse_session_holders.release_holder_remote",
        lambda **_k: True,
    )
    row = _mint()
    rid = row.registration_id
    active = reg._load_active()
    active[rid]["chat_url"] = _CSE
    active[rid]["seat_closed_at"] = 1.0
    reg._store.write_active(active)
    reg._store.append_log(
        "standdown_pasted",
        {"registration_id": rid, "chat_url": _CSE, "pasted_at": 1.0},
    )
    reg.detach(rid, reason="hop_done")
    assert rid not in reg._load_active()
    log = (isolated_registry / "registry.jsonl").read_text()
    assert "detached" in log


def test_ac9_seat_open_refused(isolated_registry: Path) -> None:
    row = _mint()
    rid = row.registration_id
    assert reg.bind_session_address(rid, chat_url=_CSE)
    with pytest.raises(ProtocolError) as exc:
        reg.detach(rid, reason="test")
    assert exc.value.code == "seat.open_on_detach"
    assert rid in reg._load_active()


def test_ac15_followup_single_pair(isolated_registry: Path) -> None:
    row = _mint()
    assert reg.bind_session_address(row.registration_id, chat_url=_CSE)
    from cdp_ask.followup_resolve import _registry_pairs_for_chat_url

    pairs = _registry_pairs_for_chat_url(_CSE)
    assert len(pairs) == 1
    assert pairs[0].registration_id == row.registration_id


def test_dead_orphaned_claim_does_not_block_rebind(
    isolated_registry: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A dead orphaned_alive row is not an attachment for the Cowork URL."""
    monkeypatch.setattr("claude_bundles.cdp_lane.is_listening", lambda _port: False)
    dead = _mint(holder="dead")
    assert reg.bind_session_address(dead.registration_id, chat_url=_CSE)
    reg.deregister_lane(
        dead.registration_id,
        reason="cse_not_found",
        is_listening=lambda _port: True,
    )
    assert reg._load_active()[dead.registration_id]["status"] == "orphaned_alive"
    live = _mint(holder="live")
    from cdp_ask.followup_reattach import _bind_chat_url

    assert _bind_chat_url(live.registration_id, _CSE) is None
    assert reg._load_active()[dead.registration_id]["status"] == "released"
    assert reg._load_active()[live.registration_id]["chat_url"] == _CSE


def test_live_holder_bind_returns_conflict(isolated_registry: Path) -> None:
    holder = _mint(holder="holder")
    other = _mint(holder="other")
    assert reg.bind_session_address(holder.registration_id, chat_url=_CSE)
    from cdp_ask.followup_reattach import _bind_chat_url

    assert _bind_chat_url(other.registration_id, _CSE) == "attachment.conflict"
    assert reg._load_active()[holder.registration_id]["status"] == "active"
    assert not reg._load_active()[other.registration_id].get("chat_url")


def test_attachment_for_chat_url_lookup(isolated_registry: Path) -> None:
    row = _mint()
    assert reg.bind_session_address(row.registration_id, chat_url=_CSE)
    found = attachment_for_chat_url(_CSE)
    assert found is not None
    assert found.registration_id == row.registration_id
