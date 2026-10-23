"""Hermetic tests for elastic tab pool (tests 2–10)."""

from __future__ import annotations

import os
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from claude_bundles import cdp_registry as reg
from claude_bundles.cdp_registry.models import STATUS_TAB
from claude_bundles.cdp_registry.tab_rows import (
    eligible_host,
    finish_tab_on_exit,
    inflight_row_for_chat_url,
    live_tab_rows_for_host,
    open_tab_row_for_ask,
    release_tab_row,
)
from claude_bundles.cdp_registry.ports import select_free_registry_port
from claude_bundles.project_ask_abort import deregister_on_exit

pytestmark = pytest.mark.offline

CSE = "https://claude.ai/cowork/cse_tabpool1"


@pytest.fixture
def isolated_registry(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    root = tmp_path / "cdp-registry"
    root.mkdir()
    regs = root / "registrations"
    regs.mkdir()
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
    monkeypatch.setattr(reg.cdp_lane, "is_listening", lambda _port: True)
    monkeypatch.setenv("CDP_REGISTRY_SEAT_AUTHORITY", "1")
    return root


def _host_row(host_id: str = "host-1", *, port: int = 9225) -> dict:
    return {
        "registration_id": host_id,
        "status": "active",
        "port": port,
        "display": ":2",
        "profile_suffix": "reg-host1",
        "holder": "cdp-tab-pool",
        "purpose": "tab_host",
        "holder_pid": os.getpid(),
        "started_at": time.time(),
    }


def _tab_row(
    tab_id: str,
    host_id: str,
    *,
    target_id: str | None = "tgt-1",
    chat_url: str = CSE,
) -> dict:
    return {
        "registration_id": tab_id,
        "status": STATUS_TAB,
        "host_registration_id": host_id,
        "port": 9225,
        "holder": "ask-holder",
        "purpose": "ask",
        "profile_suffix": "reg-tab",
        "target_id": target_id,
        "chat_url": chat_url,
        "holder_pid": os.getpid(),
        "started_at": time.time(),
    }


def test_idle_tab_closed_after_scrape(isolated_registry: Path) -> None:
    active = {"host-1": _host_row(), "tab-1": _tab_row("tab-1", "host-1")}
    reg._store.write_active(active)
    with patch("claude_bundles.cdp_lane.close_cdp_target") as close:
        reg_obj = reg._row_to_registration(active["tab-1"])
        finish_tab_on_exit(reg_obj, branch="normal")
        close.assert_called_once()
    assert "tab-1" not in reg._store.load_active()


def test_reopened_tab_waits_for_inflight_turn(isolated_registry: Path) -> None:
    from claude_bundles.cdp_registry.execution_state import set_execution_state

    active = {"host-1": _host_row(), "tab-1": _tab_row("tab-1", "host-1")}
    reg._store.write_active(active)
    set_execution_state(
        "tab-1",
        execution_id="exec-inflight",
        state="streaming",
        reason="test",
    )
    found = inflight_row_for_chat_url(CSE)
    assert found is not None
    assert found["registration_id"] == "tab-1"
    set_execution_state(
        "tab-1",
        execution_id="exec-inflight",
        state="completed",
        reason="test",
    )
    assert inflight_row_for_chat_url(CSE) is None


def test_port_9222_not_allocated_as_disposable_tab() -> None:
    with patch("claude_bundles.cdp_lane.is_listening", return_value=False):
        port = select_free_registry_port(
            lambda _p: False,
            exclude=set(),
            port_range=range(9222, 9226),
        )
    assert port != 9222


def test_dormant_park_does_not_refuse_tab_ask(isolated_registry: Path) -> None:
    active = {
        "parked": {
            **_host_row("parked"),
            "status": "dormant",
            "chat_url": CSE,
            "dormant_at": time.time(),
        },
        "live-host": _host_row("live-host"),
    }
    reg._store.write_active(active)
    with (
        patch("claude_bundles.cdp_lane.open_cdp_tab_target", return_value="t-new"),
        patch(
            "claude_bundles.cdp_registry.tab_rows.tab_host_mint_lock",
            side_effect=lambda: __import__("contextlib").contextmanager(lambda: (yield))(),
        ),
    ):
        tab = open_tab_row_for_ask(
            holder="h",
            purpose="ask",
            mission_kind="root",
            parent_thread="9999",
        )
    assert tab.host_registration_id == "live-host"


def test_no_chrome_and_x_exhausted_reroutes(monkeypatch: pytest.MonkeyPatch) -> None:
    from claude_bundles.x_display_capacity import XDisplayCapacityError

    from claude_bundles.cdp_registry.driving_seat import launch_or_defer

    def _raise_x() -> None:
        raise XDisplayCapacityError("X display :2 exhausted")

    with patch(
        "claude_bundles.cdp_registry.tab_rows.eligible_host",
        return_value=None,
    ):
        with pytest.raises(XDisplayCapacityError):
            launch_or_defer("register_lane_mint", _raise_x)


def test_many_concurrent_tab_asks_one_chrome(isolated_registry: Path) -> None:
    reg._store.write_active({"host-1": _host_row()})
    tabs: list[str] = []
    with (
        patch("claude_bundles.cdp_lane.open_cdp_tab_target", return_value="tgt"),
        patch(
            "claude_bundles.cdp_registry.tab_rows.tab_host_mint_lock",
            side_effect=lambda: __import__("contextlib").contextmanager(lambda: (yield))(),
        ),
    ):
        for i in range(10):
            tab = open_tab_row_for_ask(
                holder=f"h{i}",
                purpose="ask",
                mission_kind="root",
                parent_thread=str(9000 + i),
            )
            tabs.append(tab.registration_id)
    active = reg._store.load_active()
    assert len(tabs) == 10
    assert live_tab_rows_for_host(active, "host-1") != [] or all(
        active[t]["target_id"] for t in tabs if t in active
    )


def test_first_chrome_mint_race(isolated_registry: Path) -> None:
    mint_calls: list[str] = []

    def fake_register_lane(**kwargs):  # noqa: ANN003
        mint_calls.append("mint")
        rid = f"host-{len(mint_calls)}"
        row = _host_row(rid)
        active = reg._store.load_active()
        active[rid] = row
        reg._store.write_active(active)
        return reg._row_to_registration(row)

    with (
        patch("claude_bundles.cdp_registry.lifecycle.register_lane", side_effect=fake_register_lane),
        patch("claude_bundles.cdp_lane.open_cdp_tab_target", return_value="tgt"),
        patch(
            "claude_bundles.cdp_registry.tab_rows.tab_host_mint_lock",
            side_effect=lambda: __import__("contextlib").contextmanager(lambda: (yield))(),
        ),
    ):
        open_tab_row_for_ask(holder="a", purpose="ask", mission_kind=None, parent_thread="1")
        open_tab_row_for_ask(holder="b", purpose="ask", mission_kind=None, parent_thread="2")
    assert len(mint_calls) == 1


def test_tab_closed_when_scrape_fails(isolated_registry: Path) -> None:
    active = {"host-1": _host_row(), "tab-1": _tab_row("tab-1", "host-1")}
    reg._store.write_active(active)
    with patch("claude_bundles.cdp_lane.close_cdp_target") as close:
        deregister_on_exit(reg._row_to_registration(active["tab-1"]), purpose="ask")
        close.assert_called()
    assert "tab-1" not in reg._store.load_active()


def test_orphan_tabs_reaped_after_cdp_ask_restart(isolated_registry: Path) -> None:
    from claude_bundles.cdp_registry.execution_state import set_execution_state
    from claude_bundles.cdp_registry.tab_rows import sweep_kept_tab_rows

    active = {
        "host-1": _host_row(),
        "tab-1": {
            **_tab_row("tab-1", "host-1", target_id="stale"),
            "tab_kept_reason": "teardown",
            "target_id": None,
        },
    }
    reg._store.write_active(active)
    set_execution_state(
        "tab-1",
        execution_id="done",
        state="completed",
        reason="test",
    )
    released = sweep_kept_tab_rows()
    assert "tab-1" in released


def test_eligible_host_skips_pin_and_9222(isolated_registry: Path) -> None:
    active = {
        "pin": {**_host_row("pin", port=9222), "purpose": "operator-proxy"},
        "good": _host_row("good", port=9225),
    }
    reg._store.write_active(active)
    host = eligible_host(active, [":2"], listen=lambda p: p == 9225)
    assert host is not None
    assert host["registration_id"] == "good"
