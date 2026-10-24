"""Hermetic tests for elastic tab pool (tests 2–11)."""

from __future__ import annotations

import asyncio
import concurrent.futures
import os
import threading
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from claude_bundles import cdp_registry as reg
from claude_bundles.cdp_registry.models import STATUS_TAB
from claude_bundles.cdp_registry.ports import select_free_registry_port
from claude_bundles.cdp_registry.tab_rows import (
    eligible_host,
    finish_tab_on_exit,
    inflight_row_for_chat_url,
    live_tab_rows_for_host,
    open_tab_row_for_ask,
)
from claude_bundles.cdp_registry.tab_rows_reopen import select_page_by_target_id

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
    mint_lock = tmp_path / "tab_host_mint.lock"
    monkeypatch.setattr(
        "claude_bundles.cdp_registry.tab_rows._TAB_MINT_LOCK",
        mint_lock,
    )
    return root


def _host_row(
    host_id: str = "host-1",
    *,
    port: int = 9225,
    account_id: str | None = None,
) -> dict:
    row = {
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
    if account_id is not None:
        row["account_id"] = account_id
    return row


def _tab_row(
    tab_id: str,
    host_id: str,
    *,
    target_id: str | None = "tgt-1",
    chat_url: str = CSE,
    port: int = 9225,
    holder_pid: int | None = None,
) -> dict:
    return {
        "registration_id": tab_id,
        "status": STATUS_TAB,
        "host_registration_id": host_id,
        "port": port,
        "holder": "ask-holder",
        "purpose": "ask",
        "profile_suffix": "reg-tab",
        "target_id": target_id,
        "chat_url": chat_url,
        "holder_pid": holder_pid if holder_pid is not None else os.getpid(),
        "started_at": time.time(),
    }


def test_deregister_lane_tab_row_does_not_deadlock(isolated_registry: Path) -> None:
    active = {
        "host-1": _host_row(),
        "tab-1": _tab_row("tab-1", "host-1", target_id="tgt-dl"),
    }
    reg._store.write_active(active)

    def _run() -> None:
        reg.deregister_lane("tab-1", reason="probe_failed")

    with patch("claude_bundles.cdp_lane.close_cdp_target") as close_mock:
        t = threading.Thread(target=_run)
        t.start()
        t.join(timeout=3.0)
        assert not t.is_alive()
        close_mock.assert_called_once_with(9225, "tgt-dl")
    assert "tab-1" not in reg._store.load_active()


def test_idle_tab_closed_after_scrape(isolated_registry: Path) -> None:
    active = {"host-1": _host_row(), "tab-1": _tab_row("tab-1", "host-1")}
    reg._store.write_active(active)
    with patch("claude_bundles.cdp_lane.close_cdp_target") as close:
        reg_obj = reg._row_to_registration(active["tab-1"])
        finish_tab_on_exit(reg_obj, branch="normal")
        close.assert_called_once_with(9225, "tgt-1")
    after = reg._store.load_active()
    assert "tab-1" not in after
    assert after["host-1"]["status"] == "active"


@pytest.mark.asyncio
async def test_reopened_tab_waits_for_inflight_turn(
    isolated_registry: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cdp_ask.followup_reattach import ReattachOutcome, ensure_cse_attached

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

    monkeypatch.setattr("cdp_ask.followup_reattach._REOPEN_INFLIGHT_POLL_S", 0.02)

    async def _fake_wake(*_a, **_k) -> ReattachOutcome:
        return ReattachOutcome(
            ok=True, registration_id="tab-1", cdp_url="http://127.0.0.1:9225"
        )

    with (
        patch(
            "claude_bundles.cdp_registry.session_address.open_lane_seat_holder",
            return_value=(None, None),
        ),
        patch("cdp_ask.followup_reattach._wake_dormant_seat", side_effect=_fake_wake),
    ):
        task = asyncio.create_task(
            ensure_cse_attached(CSE, holder="reattach-caller", purpose="ask")
        )
        await asyncio.sleep(0.05)
        assert not task.done()
        set_execution_state(
            "tab-1",
            execution_id="exec-inflight",
            state="completed",
            reason="test",
        )
        outcome = await task
    assert outcome.ok


def test_port_9222_not_allocated_as_disposable_tab(isolated_registry: Path) -> None:
    active = {
        "pin": {**_host_row("pin", port=9222), "purpose": "operator-proxy"},
        "good": _host_row("good", port=9225),
    }
    reg._store.write_active(active)
    with patch("claude_bundles.cdp_lane.is_listening", return_value=False):
        port = select_free_registry_port(
            lambda _p: False,
            exclude=set(),
            port_range=range(9222, 9226),
        )
    assert port != 9222
    host = eligible_host(active, [":2"], listen=lambda p: p == 9225)
    assert host is not None
    assert host["registration_id"] == "good"


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
    with patch("claude_bundles.cdp_lane.open_cdp_tab_target", return_value="t-new"):
        tab = open_tab_row_for_ask(
            holder="h",
            purpose="ask",
            mission_kind="root",
            parent_thread="9999",
        )
    assert tab.host_registration_id == "live-host"


def test_no_chrome_and_x_exhausted_reroutes(
    isolated_registry: Path,
) -> None:
    from claude_bundles.cdp_registry.driving_seat import launch_or_defer
    from claude_bundles.x_display_capacity import XDisplayCapacityError

    def _raise_x() -> None:
        raise XDisplayCapacityError("X display :2 exhausted")

    with patch(
        "claude_bundles.cdp_registry.tab_rows.eligible_host",
        return_value=None,
    ):
        with pytest.raises(XDisplayCapacityError):
            launch_or_defer("register_lane_mint", _raise_x)
        with patch(
            "claude_bundles.cdp_registry.lifecycle.register_lane",
            side_effect=lambda **_kw: _raise_x(),
        ):
            with pytest.raises(XDisplayCapacityError):
                open_tab_row_for_ask(
                    holder="h",
                    purpose="ask",
                    mission_kind="root",
                    parent_thread="1",
                )


def test_many_concurrent_tab_asks_one_chrome(
    isolated_registry: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reg._store.write_active({"host-1": _host_row()})
    monkeypatch.setattr(
        "claude_bundles.x_display_capacity.probe_x_display",
        lambda **kw: {
            "x_display": ":2",
            "x_clients": 62,
            "x_max_clients": 64,
            "x_headroom": 2,
            "x_exhausted": True,
            "x_chrome_client_budget": 8,
            "x_probe": "injected",
        },
    )
    barrier = threading.Barrier(10)
    results: list[reg.Registration] = []

    def _ask(idx: int) -> reg.Registration:
        barrier.wait(timeout=5)
        return open_tab_row_for_ask(
            holder=f"h{idx}",
            purpose="ask",
            mission_kind="root",
            parent_thread=str(9000 + idx),
        )

    target_ids = iter(f"tgt-{i}" for i in range(10))

    def _fake_open(_port: int, _url: str) -> str:
        return next(target_ids)

    with (
        patch("claude_bundles.cdp_lane.open_cdp_tab_target", side_effect=_fake_open),
        concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool,
    ):
        futs = [pool.submit(_ask, i) for i in range(10)]
        results = [f.result(timeout=30) for f in futs]
    active = reg._store.load_active()
    assert len(results) == 10
    assert all(r.host_registration_id == "host-1" for r in results)
    assert all(
        active[r.registration_id]["status"] == STATUS_TAB
        for r in results
        if r.registration_id in active
    )
    assert len(live_tab_rows_for_host(active, "host-1")) == 10


def test_first_chrome_mint_race(isolated_registry: Path) -> None:
    mint_calls: list[str] = []

    def fake_register_lane(**kwargs):  # noqa: ANN003
        mint_calls.append("mint")
        rid = "host-1"
        row = _host_row(rid)
        active = reg._store.load_active()
        active[rid] = row
        reg._store.write_active(active)
        return reg._row_to_registration(row)

    barrier = threading.Barrier(2)
    out: list[reg.Registration] = []

    def _worker(holder: str) -> None:
        barrier.wait(timeout=5)
        out.append(
            open_tab_row_for_ask(
                holder=holder,
                purpose="ask",
                mission_kind=None,
                parent_thread=holder,
            )
        )

    with (
        patch(
            "claude_bundles.cdp_registry.lifecycle.register_lane",
            side_effect=fake_register_lane,
        ),
        patch("claude_bundles.cdp_lane.open_cdp_tab_target", return_value="tgt"),
    ):
        t1 = threading.Thread(target=_worker, args=("a",))
        t2 = threading.Thread(target=_worker, args=("b",))
        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)
    assert len(mint_calls) == 1
    assert len(out) == 2
    assert out[0].host_registration_id == out[1].host_registration_id == "host-1"


@pytest.mark.asyncio
async def test_tab_closed_when_scrape_fails(
    isolated_registry: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cdp_ask.models import SubmitProjectAskRequest
    from cdp_ask.runner import run_execution

    from claude_bundles.project_ask import ProjectAskResult

    reg._store.write_active({"host-1": _host_row()})
    monkeypatch.setattr(reg.cdp_lane, "is_listening", lambda _p: True)

    fail = ProjectAskResult(
        ok=False,
        body="",
        url="",
        project_uuid="proj",
        project_url="",
        model={},
        body_len=0,
        delete_after=True,
        error="scrape timed out",
    )

    with (
        patch("claude_bundles.cdp_lane.open_cdp_tab_target", return_value="tgt-fail"),
        patch("cdp_ask.runner.run_project_ask", new=AsyncMock(return_value=fail)),
        patch("cdp_ask.runner.registration_has_wake_debt", return_value=False),
        patch("claude_bundles.cdp_lane.close_cdp_target") as close,
    ):
        payload = await run_execution(
            SubmitProjectAskRequest(
                prompt_text="x",
                project_uuid="proj",
                purpose="ask",
                holder="h",
            ),
            execution_id="exec-fail",
            abort_check=AsyncMock(return_value=False),
        )
        assert payload["ok"] is False
        close.assert_called_with(9225, "tgt-fail")
    active = reg._store.load_active()
    assert live_tab_rows_for_host(active, "host-1") == []

    retain_a = ProjectAskResult(
        ok=False,
        body="",
        url=CSE,
        project_uuid="p",
        project_url="https://claude.ai/new",
        model={},
        body_len=0,
        delete_after=None,
        error="scrape timed out",
    )
    with (
        patch(
            "claude_bundles.cdp_lane.open_cdp_tab_target", return_value="tgt-retain-a"
        ),
        patch("cdp_ask.runner.run_project_ask", new=AsyncMock(return_value=retain_a)),
        patch("cdp_ask.runner.registration_has_wake_debt", return_value=False),
        patch("claude_bundles.cdp_lane.close_cdp_target") as close_a,
    ):
        await run_execution(
            SubmitProjectAskRequest(
                prompt_text="x",
                project_uuid="p",
                purpose="ask",
                holder="h",
            ),
            execution_id="exec-retain-a",
            abort_check=AsyncMock(return_value=False),
        )
        close_a.assert_called_with(9225, "tgt-retain-a")
    active = reg._store.load_active()
    tab_rows = [
        row
        for row in active.values()
        if isinstance(row, dict) and row.get("status") == STATUS_TAB
    ]
    assert len(tab_rows) == 1
    kept = tab_rows[0]
    assert kept.get("target_id") is None
    assert kept.get("tab_kept_reason") == "retain_host"
    assert live_tab_rows_for_host(active, "host-1") == []

    from claude_bundles.project_ask import HarvestArchiveError

    ok_turn = ProjectAskResult(
        ok=True,
        body="ans",
        url=CSE,
        project_uuid="",
        project_url="https://claude.ai/new",
        model={},
        body_len=3,
        delete_after=None,
    )

    with (
        patch(
            "claude_bundles.cdp_lane.open_cdp_tab_target", return_value="tgt-retain-b"
        ),
        patch(
            "cdp_ask.runner.run_project_conversation",
            new=AsyncMock(return_value=[ok_turn]),
        ),
        patch(
            "cdp_ask.runner.archive_harvest",
            side_effect=HarvestArchiveError("mismatch"),
        ),
        patch("cdp_ask.runner.registration_has_wake_debt", return_value=False),
        patch("claude_bundles.cdp_lane.close_cdp_target") as close_b,
    ):
        await run_execution(
            SubmitProjectAskRequest(
                prompt_text="x",
                project_uuid="p",
                purpose="ask",
                holder="h",
                converse=True,
            ),
            execution_id="exec-retain-b",
            abort_check=AsyncMock(return_value=False),
        )
        close_b.assert_called_with(9225, "tgt-retain-b")
    active = reg._store.load_active()
    tab_rows = [
        row
        for row in active.values()
        if isinstance(row, dict) and row.get("status") == STATUS_TAB
    ]
    assert len(tab_rows) == 2
    kept_b = next(r for r in tab_rows if r.get("tab_kept_reason") == "retain_host")
    assert kept_b.get("target_id") is None
    assert live_tab_rows_for_host(active, "host-1") == []


def test_abort_tab_ask_stops_own_target_and_cleans_up(
    isolated_registry: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from claude_bundles import project_ask_abort as paa
    from claude_bundles.project_ask_abort import AttestResult

    active = {
        "host-1": _host_row(),
        "tab-other": _tab_row(
            "tab-other", "host-1", target_id="tgt-other", chat_url=CSE + "-other"
        ),
        "tab-abort": _tab_row("tab-abort", "host-1", target_id="tgt-abort"),
    }
    reg._store.write_active(active)
    paa._ABORT_DONE = False
    monkeypatch.setattr(
        "claude_bundles.cse_wake_retain.registration_has_wake_debt",
        lambda _rid: False,
    )
    stop_targets: list[str | None] = []

    def _fake_stop(_url: str, *, target_id: str | None = None, **_kw: object):
        stop_targets.append(target_id)
        return AttestResult(has_stop=False, probe_ok=True)

    monkeypatch.setattr(paa, "bounded_stop_via_cdp", _fake_stop)
    reg_abort = paa.lookup_active_registration("tab-abort")
    assert reg_abort is not None
    assert reg_abort.target_id == "tgt-abort"

    with patch("claude_bundles.cdp_lane.close_cdp_target") as close:
        outcome = paa.abort_cleanup(reg_abort, purpose="ask")
        assert outcome == "attested_stopped_and_deregistered"
        assert stop_targets == ["tgt-abort"]
        close.assert_called_with(9225, "tgt-abort")

    after = reg._store.load_active()
    assert "tab-abort" not in after
    assert after["tab-other"]["target_id"] == "tgt-other"


@pytest.mark.asyncio
async def test_abort_tab_run_execution_closes_target(
    isolated_registry: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cdp_ask.models import SubmitProjectAskRequest
    from cdp_ask.runner import run_execution

    from claude_bundles.project_ask import ProjectAskResult

    reg._store.write_active({"host-1": _host_row()})
    monkeypatch.setattr(reg.cdp_lane, "is_listening", lambda _p: True)
    ok = ProjectAskResult(
        ok=True,
        body="x",
        url=CSE,
        project_uuid="p",
        project_url="https://claude.ai/new",
        model={},
        body_len=1,
        delete_after=None,
    )
    with (
        patch(
            "claude_bundles.cdp_lane.open_cdp_tab_target", return_value="tgt-abort-run"
        ),
        patch("cdp_ask.runner.run_project_ask", new=AsyncMock(return_value=ok)),
        patch("cdp_ask.runner.registration_has_wake_debt", return_value=False),
        patch("claude_bundles.cdp_lane.close_cdp_target") as close,
    ):
        await run_execution(
            SubmitProjectAskRequest(
                prompt_text="x",
                project_uuid="p",
                purpose="ask",
                holder="h",
            ),
            execution_id="exec-abort-tab",
            abort_check=AsyncMock(side_effect=[False, True]),
        )
        close.assert_called_with(9225, "tgt-abort-run")
    active = reg._store.load_active()
    tab_rows = [
        row
        for row in active.values()
        if isinstance(row, dict) and row.get("status") == STATUS_TAB
    ]
    assert len(tab_rows) == 1
    assert tab_rows[0].get("target_id") is None
    assert tab_rows[0].get("tab_kept_reason") == "abort_unattested"
    assert live_tab_rows_for_host(active, "host-1") == []


def test_orphan_tabs_reaped_after_cdp_ask_restart(
    isolated_registry: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from claude_bundles.cdp_registry.dormant import reap_dead_holder_tab_rows

    active = {
        "host-1": {**_host_row("host-1", port=9228), "port": 9228},
        "operator-seat": {
            "registration_id": "operator-seat",
            "status": "active",
            "port": 9225,
            "display": ":2",
            "profile_suffix": "reg-op",
            "purpose": "operator-proxy",
            "seat_lane": "12286",
            "chat_url": "https://claude.ai/cowork/cse_operator",
            "holder_pid": os.getpid(),
            "started_at": time.time(),
        },
        "dead-tab": _tab_row(
            "dead-tab",
            "host-1",
            target_id="tgt-dead",
            port=9228,
            holder_pid=99999999,
        ),
    }
    reg._store.write_active(active)
    monkeypatch.setattr(
        "claude_bundles.cdp_registry.hygiene._pid_alive",
        lambda pid: pid != 99999999,
    )
    with patch("claude_bundles.cdp_lane.close_cdp_target") as close:
        reap_dead_holder_tab_rows()
        close.assert_called_once_with(9228, "tgt-dead")
    after = reg._store.load_active()
    assert "dead-tab" not in after
    assert after["operator-seat"]["status"] == "active"
    assert after["operator-seat"]["port"] == 9225


@pytest.mark.asyncio
async def test_multi_account_tab_routing_and_target_isolation(
    isolated_registry: Path,
) -> None:
    active = {
        "operator-seat": {
            **_host_row("operator-seat", port=9225),
            "purpose": "operator-proxy",
            "seat_lane": "12286",
        },
        "host-a": {**_host_row("host-a", port=9226, account_id="acct-a")},
        "host-b": {**_host_row("host-b", port=9227, account_id="acct-b")},
        "tab-a1": _tab_row("tab-a1", "host-a", port=9226),
        "tab-a2": _tab_row("tab-a2", "host-a", port=9226, target_id="tgt-a2"),
    }
    reg._store.write_active(active)
    host = eligible_host(active, [":2"], listen=lambda _p: True)
    assert host is not None
    assert host["registration_id"] == "host-b"

    with patch("claude_bundles.cdp_lane.open_cdp_tab_target", return_value="tgt-new"):
        picked = open_tab_row_for_ask(
            holder="h",
            purpose="ask",
            mission_kind="root",
            parent_thread="1",
            account_id="acct-a",
        )
    assert picked.host_registration_id == "host-a"

    page1 = MagicMock()
    page1.target_id = "tgt-1"
    page1.bring_to_front = MagicMock()
    page2 = MagicMock()
    page2.target_id = "tgt-2"
    page2.bring_to_front = MagicMock()
    ctx = MagicMock()
    ctx.pages = [page1, page2]

    page = await select_page_by_target_id(ctx, "tgt-2")
    assert page is page2
    page2.bring_to_front.assert_not_called()

    pw = MagicMock()
    pw.stop = AsyncMock()

    with (
        patch(
            "claude_bundles.project_ask.connect_cdp",
            new=AsyncMock(return_value=(pw, MagicMock(), ctx, page1)),
        ),
        patch(
            "claude_bundles.project_ask.pick_chat_page",
            new=AsyncMock(side_effect=AssertionError("pick_chat_page")),
        ),
        patch(
            "claude_bundles.project_ask.project_ask_on_page",
            new=AsyncMock(
                return_value=MagicMock(ok=True, body="x", url="", project_uuid="")
            ),
        ),
    ):
        from claude_bundles.project_ask import run_project_ask

        await run_project_ask(
            "hi", project_uuid="p", target_id="tgt-2", delete_after=False
        )
