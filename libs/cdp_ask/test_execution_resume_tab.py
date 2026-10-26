"""Resume legs for kept STATUS_TAB rows (G-E)."""

from __future__ import annotations

import asyncio
import os
import threading
import time
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from claude_bundles import cdp_registry as reg
from claude_bundles.cdp_registry.dormant import reap_dead_holder_tab_rows
from claude_bundles.cdp_registry.execution_state import set_execution_state
from claude_bundles.cdp_registry.models import STATUS_TAB

from cdp_ask.cse_session_models import CseSessionTurn, HarvestResponse
from cdp_ask.execution_resume import (
    _RESUMABLE_ROW_STATUSES,
    _dispose_host,
    hydrate_in_flight,
    resume_execution,
)
from cdp_ask.execution_store import ExecutionStore

pytestmark = pytest.mark.offline


def test_resumable_includes_tab_status() -> None:
    assert STATUS_TAB in _RESUMABLE_ROW_STATUSES


def test_dispose_host_tab_row_normal_branch() -> None:
    row = {
        "registration_id": "tab-r",
        "status": STATUS_TAB,
        "port": 9225,
        "profile_suffix": "reg-t",
        "holder": "h",
        "host_registration_id": "host-1",
        "target_id": "tgt",
    }
    with (
        patch("cdp_ask.execution_resume._row_for", return_value=row),
        patch("claude_bundles.cdp_registry.tab_rows.finish_tab_on_exit") as finish,
    ):
        from cdp_ask.execution_store import ExecutionRecord

        rec = ExecutionRecord(
            execution_id="e1",
            status="running",
            created_at=1.0,
            updated_at=1.0,
            registration_id="tab-r",
            holder="h",
            purpose="ask",
        )
        _dispose_host(rec, teardown=False)
        finish.assert_called_once()


class _Page:
    def __init__(self, target_id: str, url: str) -> None:
        self.target_id = target_id
        self.url = url
        self.closed = False

    async def close(self) -> None:
        self.closed = True


class _Pw:
    def __init__(self) -> None:
        self.stopped = False

    async def stop(self) -> None:
        self.stopped = True


@pytest.fixture
def tab_registry(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, Any]:
    """Isolated registry plus CDP stubs shared by the resume legs."""
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
    profiles = tmp_path / "profiles"
    profiles.mkdir()
    monkeypatch.setattr(
        reg.cdp_lane,
        "profile_for",
        lambda suffix: profiles / f"claude-ai-chrome-profile-{suffix}",
    )
    live = {9224, 9225, 9226}
    monkeypatch.setattr(reg.cdp_lane, "is_listening", lambda port: port in live)
    monkeypatch.setattr(
        "claude_bundles.cdp_registry.tab_rows._pin_port_block",
        lambda: set(),
    )
    monkeypatch.setattr(
        "claude_bundles.cdp_lane.cdp_display_candidates",
        lambda account_id=None: [":2"],
    )
    monkeypatch.setattr(
        "claude_bundles.cdp_registry.tab_rows._TAB_MINT_LOCK",
        tmp_path / "tab_host_mint.lock",
    )
    monkeypatch.setattr(
        "claude_bundles.cse_provenance.append_episode",
        lambda **_kwargs: None,
    )

    async def _label(_page: Any) -> str:
        return "stub-model"

    monkeypatch.setattr(
        "claude_bundles.chat_model_select.current_model_label",
        _label,
    )
    monkeypatch.setattr("cdp_ask.execution_resume.current_model_label", _label)
    opened: dict[str, str] = {}
    ops: list[tuple[Any, ...]] = []
    open_lock = threading.Lock()
    counter = {"n": 0}

    def fake_open(port: int, url: str) -> str:
        with open_lock:
            tid = f"t{counter['n']}"
            counter["n"] += 1
            opened[tid] = url
            ops.append(("open", port, tid, url))
        return tid

    def fake_close(port: int, tid: str) -> None:
        ops.append(("close", port, tid))
        opened.pop(tid, None)

    async def fake_connect(_cdp_url: str) -> tuple[Any, Any, Any, Any]:
        ctx = type("Ctx", (), {})()
        pages = [_Page("decoy-first", next(iter(opened.values()), ""))]
        pages.extend(_Page(tid, url) for tid, url in list(opened.items()))
        ctx.pages = pages
        return _Pw(), None, ctx, None

    monkeypatch.setattr("claude_bundles.cdp_lane.open_cdp_tab_target", fake_open)
    monkeypatch.setattr("claude_bundles.cdp_lane.close_cdp_target", fake_close)
    monkeypatch.setattr("claude_bundles.skills_ui_panel.connect_cdp", fake_connect)
    kills: list[int] = []
    monkeypatch.setattr(
        "claude_bundles.cdp_registry.lifecycle._kill_listener",
        lambda port: kills.append(port),
    )
    return {"live": live, "ops": ops, "kills": kills, "root": root}


def _host(
    host_id: str,
    port: int,
    purpose: str,
    *,
    chat_url: str | None = None,
    status: str = "active",
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "registration_id": host_id,
        "status": status,
        "port": port,
        "display": ":2",
        "profile_suffix": f"p-{host_id}",
        "holder": "operator" if purpose != "tab_host" else "cdp-tab-pool",
        "purpose": purpose,
        "holder_pid": os.getpid(),
        "started_at": time.time(),
    }
    if chat_url:
        row["chat_url"] = chat_url
    return row


def _tab(
    tab_id: str,
    host_id: str,
    port: int,
    url: str,
    *,
    target_id: str | None = "stale",
    holder_pid: int | None = None,
    kept: str | None = None,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "registration_id": tab_id,
        "status": STATUS_TAB,
        "host_registration_id": host_id,
        "port": port,
        "holder": "ask-holder",
        "purpose": "ask",
        "profile_suffix": "p-tab",
        "display": ":2",
        "target_id": target_id,
        "chat_url": url,
        "holder_pid": os.getpid() if holder_pid is None else holder_pid,
        "started_at": time.time(),
    }
    if kept:
        row["tab_kept_reason"] = kept
    return row


def _harvest_response() -> HarvestResponse:
    return HarvestResponse(
        outcome="harvested",
        streaming=False,
        content_provenance="chat",
        turns=[
            CseSessionTurn(
                author="assistant",
                text="The resumed answer is ready for the archive.",
                source="cse-dom",
            )
        ],
    )


def _stamp(tab_id: str, execution_id: str) -> None:
    set_execution_state(
        tab_id,
        execution_id=execution_id,
        state="streaming",
        reason="test-resume",
    )


async def _run_three(
    store: ExecutionStore,
    specs: list[tuple[str, str]],
    *,
    release: dict[str, asyncio.Event],
    started: dict[str, asyncio.Event],
    seen: list[str],
) -> list[asyncio.Task[None]]:
    """specs is (tab_id, execution_id). Harvest blocks until release[eid]."""

    async def harvest(page: Any, req: Any, provenance: Any = None) -> HarvestResponse:
        del provenance
        eid = str(req.execution_id)
        current = await store.get(eid)
        assert current is not None
        assert current.registration_id == next(
            tab for tab, bound in specs if bound == eid
        )
        assert page.target_id != "decoy-first"
        row = reg._store.load_active()[current.registration_id]
        assert page.target_id == row["target_id"]
        seen.append(current.registration_id or "")
        started[eid].set()
        await release[eid].wait()
        return _harvest_response()

    import cdp_ask.execution_resume as resume_mod

    resume_mod.harvest_page = harvest  # type: ignore[method-assign]
    tasks = []
    for tab_id, eid in specs:
        record = await store.adopt(
            execution_id=eid,
            registration_id=tab_id,
            holder="ask-holder",
            purpose="ask",
            parent_thread=None,
            mission_kind=None,
            started_at=time.time(),
            durable_state="streaming",
        )
        tasks.append(
            asyncio.create_task(resume_execution(store, record, trigger="boot"))
        )
    return tasks


async def _wait_attached(
    tasks: list[asyncio.Task[None]], events: list[asyncio.Event]
) -> None:
    async def _watch() -> None:
        while not all(event.is_set() for event in events):
            for task in tasks:
                if task.done() and task.exception() is not None:
                    task.result()
            await asyncio.sleep(0.01)

    await asyncio.wait_for(_watch(), timeout=5)


@pytest.mark.asyncio
async def test_tab_closed_when_scrape_fails(
    tab_registry: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Three concurrent tab resumes, plus the dead-host and shutdown variants."""
    ensure_calls: list[str] = []
    transfers: list[tuple[str, str]] = []

    async def _ensure(*_args: Any, **_kwargs: Any) -> None:
        ensure_calls.append("ensure")
        raise AssertionError("ensure_cse_attached")

    async def _transfer(self: Any, execution_id: str, registration_id: str) -> None:
        transfers.append((execution_id, registration_id))
        raise AssertionError("set_registration_id")

    monkeypatch.setattr("cdp_ask.followup_reattach.ensure_cse_attached", _ensure)
    monkeypatch.setattr(ExecutionStore, "set_registration_id", _transfer)
    dormant_calls: list[str] = []
    deregister_calls: list[str] = []
    real_deregister = reg.deregister_lane
    real_dormant = reg.make_dormant

    def _dormant(*args: Any, **_kwargs: Any) -> None:
        dormant_calls.append(str(args[0]) if args else "")

    def _deregister(*args: Any, **_kwargs: Any) -> None:
        deregister_calls.append(str(args[0]) if args else "")

    monkeypatch.setattr("claude_bundles.cdp_registry.dormant.make_dormant", _dormant)
    monkeypatch.setattr(reg, "make_dormant", _dormant)
    monkeypatch.setattr(
        "claude_bundles.cdp_registry.lifecycle.deregister_lane", _deregister
    )
    monkeypatch.setattr(reg, "deregister_lane", _deregister)

    seat_url = "https://claude.ai/cowork/cse_seat9"
    urls = {
        "T1": "https://claude.ai/cowork/cse_tab9a",
        "T2": "https://claude.ai/cowork/cse_tab9b",
        "T3": "https://claude.ai/cowork/cse_tab9c",
    }
    reg._store.write_active(
        {
            "H": _host("H", 9225, "tab_host"),
            "S": _host("S", 9224, "operator-proxy", chat_url=seat_url),
            "T1": _tab("T1", "H", 9225, urls["T1"], target_id="old-1"),
            "T2": _tab("T2", "H", 9225, urls["T2"], target_id="old-2"),
            "T3": _tab("T3", "H", 9225, urls["T3"], target_id="old-3"),
        }
    )
    _stamp("S", "exec-seat")
    for tab_id in ("T1", "T2", "T3"):
        _stamp(tab_id, f"exec-{tab_id}")

    store = ExecutionStore()
    release = {f"exec-{tab}": asyncio.Event() for tab in ("T1", "T2", "T3")}
    started = {f"exec-{tab}": asyncio.Event() for tab in ("T1", "T2", "T3")}
    seen: list[str] = []
    specs = [(tab, f"exec-{tab}") for tab in ("T1", "T2", "T3")]
    tasks = await _run_three(store, specs, release=release, started=started, seen=seen)
    await _wait_attached(tasks, list(started.values()))
    assert ensure_calls == []
    assert transfers == []
    assert seen == ["T1", "T2", "T3"] or set(seen) == {"T1", "T2", "T3"}
    active = reg._store.load_active()
    assert "chat_url" not in active["H"]
    assert active["S"]["chat_url"] == seat_url
    assert tab_registry["kills"] == []
    assert dormant_calls == []
    assert deregister_calls == []
    for tab in ("T1", "T2", "T3"):
        record = await store.get(f"exec-{tab}")
        assert record is not None
        assert record.registration_id == tab

    release["exec-T1"].set()
    await asyncio.wait_for(tasks[0], timeout=5)
    active = reg._store.load_active()
    assert "T1" not in active
    assert active["T2"]["target_id"]
    assert active["T3"]["target_id"]
    assert "chat_url" not in active["H"]

    release["exec-T2"].set()
    release["exec-T3"].set()
    await asyncio.wait_for(tasks[1], timeout=5)
    await asyncio.wait_for(tasks[2], timeout=5)
    active = reg._store.load_active()
    assert [rid for rid, row in active.items() if row.get("status") == STATUS_TAB] == []
    assert active["S"]["chat_url"] == seat_url
    assert tab_registry["kills"] == []

    monkeypatch.setattr(
        "claude_bundles.cdp_registry.lifecycle.deregister_lane", real_deregister
    )
    monkeypatch.setattr(reg, "deregister_lane", real_deregister)
    monkeypatch.setattr(
        "claude_bundles.cdp_registry.dormant.make_dormant", real_dormant
    )
    monkeypatch.setattr(reg, "make_dormant", real_dormant)
    monkeypatch.setattr(
        "claude_bundles.cdp_orphans._fetch_json",
        lambda _url: [],
    )
    monkeypatch.setattr(
        "claude_bundles.cdp_registry.session_address._default_probe_page_urls",
        lambda _port: [],
    )
    from cdp_ask.registry_hygiene_loop import run_hygiene_once

    run_hygiene_once()
    active = reg._store.load_active()
    host = active.get("H")
    assert host is None or host.get("status") == "released"
    assert active["S"]["chat_url"] == seat_url

    # H dead: pool tabs mint a shared tab_host (operator-proxy is not a pool host).
    tab_registry["kills"].clear()
    tab_registry["live"].discard(9225)
    tab_registry["live"].add(9226)

    def _mint_pool_host(**_kwargs: Any) -> reg.Registration:
        row = _host("Pool", 9226, "tab_host")
        active = reg._store.load_active()
        active["Pool"] = row
        reg._store.write_active(active)
        return reg._row_to_registration(row)

    monkeypatch.setattr(
        "claude_bundles.cdp_registry.lifecycle.register_lane",
        _mint_pool_host,
    )
    reg._store.write_active(
        {
            "H": _host("H", 9225, "tab_host", status="released"),
            "S": _host("S", 9224, "operator-proxy", chat_url=seat_url),
            "T1": _tab("T1", "H", 9225, urls["T1"], target_id="old-1"),
            "T2": _tab("T2", "H", 9225, urls["T2"], target_id="old-2"),
            "T3": _tab("T3", "H", 9225, urls["T3"], target_id="old-3"),
        }
    )
    for tab_id in ("T1", "T2", "T3"):
        _stamp(tab_id, f"dead-{tab_id}")
    store = ExecutionStore()
    release = {f"dead-{tab}": asyncio.Event() for tab in ("T1", "T2", "T3")}
    started = {f"dead-{tab}": asyncio.Event() for tab in ("T1", "T2", "T3")}
    hosts_seen: list[str] = []

    async def harvest_on_s(
        page: Any, req: Any, provenance: Any = None
    ) -> HarvestResponse:
        del page, provenance
        eid = str(req.execution_id)
        current = await store.get(eid)
        assert current is not None
        row = reg._store.load_active()[str(current.registration_id)]
        hosts_seen.append(str(row["host_registration_id"]))
        started[eid].set()
        await release[eid].wait()
        return _harvest_response()

    import cdp_ask.execution_resume as resume_mod

    resume_mod.harvest_page = harvest_on_s  # type: ignore[method-assign]
    tasks = []
    for tab_id in ("T1", "T2", "T3"):
        record = await store.adopt(
            execution_id=f"dead-{tab_id}",
            registration_id=tab_id,
            holder="ask-holder",
            purpose="ask",
            parent_thread=None,
            mission_kind=None,
            started_at=time.time(),
            durable_state="streaming",
        )
        tasks.append(
            asyncio.create_task(resume_execution(store, record, trigger="host_lost"))
        )
    await _wait_attached(tasks, list(started.values()))
    assert set(hosts_seen) == {"Pool"}
    assert 9224 not in tab_registry["kills"]
    assert 9225 not in tab_registry["kills"]
    for event in release.values():
        event.set()
    await asyncio.wait_for(asyncio.gather(*tasks), timeout=5)

    # H dead and S excluded (port 9222): one pool-host mint across the three resumes.
    tab_registry["kills"].clear()
    tab_registry["live"].add(9226)
    mint_calls: list[str] = []

    def fake_register(**kwargs: Any) -> Any:
        mint_calls.append(str(kwargs.get("purpose")))
        minted = _host("host-mint", 9226, "tab_host")
        active_now = reg._store.load_active()
        active_now["host-mint"] = minted
        reg._store.write_active(active_now)
        return reg._row_to_registration(minted)

    monkeypatch.setattr(
        "claude_bundles.cdp_registry.lifecycle.register_lane", fake_register
    )
    reg._store.write_active(
        {
            "H": _host("H", 9225, "tab_host", status="released"),
            "S": _host("S", 9222, "operator-proxy", chat_url=seat_url),
            "T1": _tab("T1", "H", 9225, urls["T1"], target_id=None),
            "T2": _tab("T2", "H", 9225, urls["T2"], target_id=None),
            "T3": _tab("T3", "H", 9225, urls["T3"], target_id=None),
        }
    )
    for tab_id in ("T1", "T2", "T3"):
        _stamp(tab_id, f"mint-{tab_id}")
    store = ExecutionStore()
    release = {f"mint-{tab}": asyncio.Event() for tab in ("T1", "T2", "T3")}
    started = {f"mint-{tab}": asyncio.Event() for tab in ("T1", "T2", "T3")}
    minted_hosts: list[str] = []

    async def harvest_minted(
        page: Any, req: Any, provenance: Any = None
    ) -> HarvestResponse:
        del page, provenance
        eid = str(req.execution_id)
        current = await store.get(eid)
        assert current is not None
        row = reg._store.load_active()[str(current.registration_id)]
        minted_hosts.append(str(row["host_registration_id"]))
        started[eid].set()
        await release[eid].wait()
        return _harvest_response()

    resume_mod.harvest_page = harvest_minted  # type: ignore[method-assign]
    tasks = []
    for tab_id in ("T1", "T2", "T3"):
        record = await store.adopt(
            execution_id=f"mint-{tab_id}",
            registration_id=tab_id,
            holder="ask-holder",
            purpose="ask",
            parent_thread=None,
            mission_kind=None,
            started_at=time.time(),
            durable_state="streaming",
        )
        tasks.append(
            asyncio.create_task(resume_execution(store, record, trigger="boot"))
        )
    await _wait_attached(tasks, list(started.values()))
    assert mint_calls == ["tab_host"]
    assert set(minted_hosts) == {"host-mint"}
    assert 9222 not in tab_registry["kills"]
    seat = reg._store.load_active()["S"]
    assert seat["chat_url"] == seat_url
    assert seat["port"] == 9222
    for event in release.values():
        event.set()
    await asyncio.wait_for(asyncio.gather(*tasks), timeout=5)

    # Shutdown mid-resume: each tab stays address-only with teardown.
    tab_registry["kills"].clear()
    tab_registry["live"].update({9225, 9224})
    reg._store.write_active(
        {
            "H": _host("H", 9225, "tab_host"),
            "T1": _tab("T1", "H", 9225, urls["T1"], target_id="shut-1"),
            "T2": _tab("T2", "H", 9225, urls["T2"], target_id="shut-2"),
            "T3": _tab("T3", "H", 9225, urls["T3"], target_id="shut-3"),
        }
    )
    for tab_id in ("T1", "T2", "T3"):
        _stamp(tab_id, f"stop-{tab_id}")
    store = ExecutionStore()
    store.shutting_down = True

    async def harvest_now(
        page: Any, req: Any, provenance: Any = None
    ) -> HarvestResponse:
        del page, req, provenance
        return _harvest_response()

    resume_mod.harvest_page = harvest_now  # type: ignore[method-assign]
    tasks = []
    for tab_id in ("T1", "T2", "T3"):
        record = await store.adopt(
            execution_id=f"stop-{tab_id}",
            registration_id=tab_id,
            holder="ask-holder",
            purpose="ask",
            parent_thread=None,
            mission_kind=None,
            started_at=time.time(),
            durable_state="streaming",
        )
        tasks.append(
            asyncio.create_task(resume_execution(store, record, trigger="boot"))
        )
    await asyncio.wait_for(asyncio.gather(*tasks), timeout=5)
    active = reg._store.load_active()
    for tab_id in ("T1", "T2", "T3"):
        row = active[tab_id]
        assert row["status"] == STATUS_TAB
        assert row["target_id"] is None
        assert row["tab_kept_reason"] == "teardown"
    assert "chat_url" not in active["H"]
    assert 9225 not in tab_registry["kills"]


@pytest.mark.asyncio
async def test_orphan_tabs_reaped_after_cdp_ask_restart(
    tab_registry: dict[str, Any],
) -> None:
    """Six in-flight tab rows: reap and hydrate commute, then resumes finish."""
    dead_pid = 99_999_999
    seat_url = "https://claude.ai/cowork/cse_seat10"
    pin_url = "https://claude.ai/cowork/cse_pin9222"

    def _six() -> dict[str, Any]:
        rows: dict[str, Any] = {
            "H": _host("H", 9225, "tab_host"),
            "S": _host("S", 9224, "operator-proxy", chat_url=seat_url),
            "P": _host("P", 9222, "operator-proxy", chat_url=pin_url),
        }
        rows["S"]["page_token"] = "seat-page"
        rows["P"]["page_token"] = "pin-page"
        for index, tab_id in enumerate(("A", "B", "C")):
            url = f"https://claude.ai/cowork/cse_orphan{index}"
            rows[tab_id] = _tab(
                tab_id,
                "H",
                9225,
                url,
                target_id=f"stale-{index}",
                holder_pid=dead_pid,
            )
        for index, tab_id in enumerate(("D", "E", "F")):
            url = f"https://claude.ai/cowork/cse_kept{index}"
            rows[tab_id] = _tab(
                tab_id,
                "H",
                9225,
                url,
                target_id=None,
                holder_pid=os.getpid(),
                kept="teardown",
            )
        return rows

    async def _order(order: str) -> None:
        tab_registry["ops"].clear()
        tab_registry["kills"].clear()
        reg._store.write_active(_six())
        ids = ("A", "B", "C", "D", "E", "F")
        for tab_id in ids:
            _stamp(tab_id, f"{order}-{tab_id}")
        store = ExecutionStore()
        if order == "hydrate-first":
            adopted = await hydrate_in_flight(store)
            assert {rec.registration_id for rec in adopted} == set(ids)
            reap_dead_holder_tab_rows()
        else:
            reap_dead_holder_tab_rows()
            adopted = await hydrate_in_flight(store)
            assert {rec.registration_id for rec in adopted} == set(ids)
        active = reg._store.load_active()
        for tab_id in ids:
            assert tab_id in active
            assert active[tab_id]["status"] == STATUS_TAB
        for index in range(3):
            assert active[("A", "B", "C")[index]].get("target_id") in {None, ""}
        assert active["S"]["page_token"] == "seat-page"
        assert active["P"]["page_token"] == "pin-page"
        assert active["P"]["port"] == 9222

        async def harvest_done(
            page: Any, req: Any, provenance: Any = None
        ) -> HarvestResponse:
            del page, req, provenance
            return _harvest_response()

        import cdp_ask.execution_resume as resume_mod

        resume_mod.harvest_page = harvest_done  # type: ignore[method-assign]
        tasks = []
        for rec in adopted:
            tasks.append(
                asyncio.create_task(resume_execution(store, rec, trigger="boot"))
            )
        await asyncio.wait_for(asyncio.gather(*tasks), timeout=5)
        ops = list(tab_registry["ops"])
        for index in range(3):
            url = f"https://claude.ai/cowork/cse_orphan{index}"
            close_at = next(
                pos
                for pos, op in enumerate(ops)
                if op[0] == "close" and op[2] == f"stale-{index}"
            )
            open_at = next(
                pos for pos, op in enumerate(ops) if op[0] == "open" and op[3] == url
            )
            assert close_at < open_at
            assert ops[close_at][1] == 9225
        closed_ports = {op[1] for op in ops if op[0] == "close"}
        assert 9224 not in closed_ports
        assert 9222 not in closed_ports
        assert tab_registry["kills"] == []
        active = reg._store.load_active()
        assert [
            rid for rid, row in active.items() if row.get("status") == STATUS_TAB
        ] == []
        assert active["S"]["chat_url"] == seat_url
        assert active["S"]["page_token"] == "seat-page"
        assert active["P"] == _six()["P"] or (
            active["P"]["chat_url"] == pin_url and active["P"]["port"] == 9222
        )

    await _order("reap-first")
    await _order("hydrate-first")
