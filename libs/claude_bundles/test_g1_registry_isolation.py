"""T0 criteria T0.1–T0.8′ — mech10 rebuilt bodies (c9/c10)."""

from __future__ import annotations

import contextlib
from pathlib import Path

from claude_bundles import cdp_lane
from claude_bundles import cdp_registry as reg

G1_TEST_PORT_RANGE = range(19100, 19110)
T0_6_CHAT_URL = "https://claude.ai/chat/cafebabe-f006-4000-8000-0000000c8f06"
T0_6_NOOP_PID = 8844243
_SPAWNED: set[int] = set()


def _noop_launch(port: int, profile: Path) -> int:
    profile.mkdir(parents=True, exist_ok=True)
    pid = 4242
    _SPAWNED.add(pid)
    return pid


def _t0_6_launch(port: int, profile: Path) -> int:
    profile.mkdir(parents=True, exist_ok=True)
    return T0_6_NOOP_PID


def _leg_logs_root(ctx) -> Path:
    for p in ctx.tmp_path.parents:
        logs = p / "logs"
        if logs.is_dir():
            return logs
    return ctx.tmp_path.parent.parent / "logs"


def _rows_since(ctx, before: int, *, kinds: tuple[str, ...] | None = None):
    rows = ctx.ledger[before:]
    if kinds:
        rows = [r for r in rows if r.get("kind") in kinds]
    return [r for r in rows if not r.get("device")]


def _row_str(row: dict) -> str:
    return str(row)


def test_t0_1_reclaim_inside_tmp(g1_registry_isolation, tmp_path: Path) -> None:
    ctx = g1_registry_isolation
    profile = tmp_path / "released-profile"
    profile.mkdir()
    rid = "rel-1"
    row = {
        "status": "released",
        "profile": str(profile),
        "profile_suffix": "released-profile",
        "port": 19101,
        "holder": "t0",
        "purpose": "operator-proxy",
    }
    assert row["status"] in {"released", "orphaned_retry"}
    assert row["port"] == int(row["port"])
    assert profile.is_dir()
    reg._store.write_active({rid: row})
    assert not ctx.reclaim_trash.is_dir()
    before = len(ctx.ledger)
    with contextlib.suppress(PermissionError):
        reg.hygiene_reclaim_released()
    assert ctx.reclaim_trash.is_dir()
    assert not profile.exists()
    logs = reg._store.read_registry_log()
    hits = [
        e
        for e in logs
        if e.get("event") == "hygiene_reclaim"
        and e.get("registration_id") == rid
        and e.get("reclaim_outcome") == "success"
        and e.get("profile_removed") is True
    ]
    assert hits, f"expected hygiene_reclaim receipt, got {logs[-5:]}"
    leg_tmp = next(p for p in tmp_path.parents if (p / "logs").is_dir())
    escape = leg_tmp / "home" / ".gateway" / ".reclaim-trash"
    assert not escape.exists(), f"escape destination {escape}"
    bad = [r for r in _rows_since(ctx, before) if str(tmp_path) not in _row_str(r)]
    assert not bad, f"writes outside tmp_path: {bad}"


def test_t0_2_registry_files_inside_tmp(g1_registry_isolation, tmp_path: Path) -> None:
    ctx = g1_registry_isolation
    before = len(ctx.ledger)
    reg.register_lane(
        holder="t0-2",
        purpose="operator-proxy",
        launch_chrome=_noop_launch,
        is_listening=lambda _p: False,
    )
    for path in (ctx.registry_root / "active.json", ctx.registry_root / "ports.lock"):
        assert path.exists(), f"missing registry artifact at {path}"
        assert str(path).startswith(str(tmp_path)), f"registry write escaped tmp_path: {path}"
    bad = [r for r in _rows_since(ctx, before) if str(tmp_path) not in _row_str(r)]
    assert not bad, f"writes outside tmp_path: {bad}"


def test_t0_3a_port_in_test_range(g1_registry_isolation) -> None:
    row = reg.register_lane(
        holder="t0-3a",
        purpose="operator-proxy",
        launch_chrome=_noop_launch,
        is_listening=lambda _p: False,
    )
    assert row.port in G1_TEST_PORT_RANGE, f"port {row.port} outside {G1_TEST_PORT_RANGE}"


def test_t0_3b_no_prod_pool_probe(g1_registry_isolation) -> None:
    ctx = g1_registry_isolation
    before = len(ctx.ledger)
    with contextlib.suppress(ConnectionRefusedError):
        reg.register_lane(
            holder="t0-3b",
            purpose="operator-proxy",
            launch_chrome=_noop_launch,
        )
    af_inet = [
        r
        for r in ctx.ledger[before:]
        if r.get("kind") == "connect" and "family" not in r and not r.get("device")
    ]
    assert not af_inet, f"AF_INET connect rows in delta: {af_inet}"



def test_t0_3c_fixture_runs(g1_registry_isolation) -> None:
    assert g1_registry_isolation.registry_root.exists()


def test_t0_4a_no_proc_access(g1_registry_isolation) -> None:
    ctx = g1_registry_isolation
    before = len(ctx.ledger)
    reg.register_lane(
        holder="t0-4a",
        purpose="operator-proxy",
        launch_chrome=_noop_launch,
        is_listening=lambda _p: False,
    )
    for row in _rows_since(ctx, before):
        assert "/proc" not in _row_str(row), f"live /proc access: {row}"
        if row.get("kind") == "connect":
            addr = row.get("address")
            if isinstance(addr, tuple) and len(addr) == 2:
                assert addr[1] not in range(9338, 9345), f"CDP pool probe {row}"


def test_t0_4b_orphan_sweep_no_proc(g1_registry_isolation, tmp_path: Path, request) -> None:
    ctx = g1_registry_isolation
    parent = cdp_lane.PRIMARY_PROFILE.parent
    prefix = f"{cdp_lane.PRIMARY_PROFILE.name}-reg-"
    leg_tmp = next(p for p in tmp_path.parents if (p / "logs").is_dir())
    assert str(parent).startswith(str(leg_tmp)), f"PRIMARY parent {parent} not under leg-tmp {leg_tmp}"
    orphan_name = f"{prefix}g1orphan"
    orphan_dir = parent / orphan_name
    reg._store.write_active(
        {
            "t0-4b-keep": {
                "status": "dormant",
                "profile_suffix": "t0-4b-keep",
                "holder": "t0-4b-keep",
                "purpose": "operator-proxy",
                "port": 19104,
            }
        }
    )
    rd = reg._store.load_active_read()
    assert rd.present, rd.miss_label()
    registered_suffixes = {row.get("profile_suffix", "") for row in rd.data.values()}
    orphan_suffix = orphan_name.removeprefix(f"{cdp_lane.PRIMARY_PROFILE.name}-")
    assert orphan_suffix not in registered_suffixes
    orphan_dir.mkdir(parents=True, exist_ok=True)

    def _cleanup() -> None:
        if orphan_dir.exists():
            orphan_dir.rmdir()

    request.addfinalizer(_cleanup)
    assert orphan_dir.is_dir()
    before = len(ctx.ledger)
    result = None
    with contextlib.suppress(PermissionError):
        result = reg.hygiene_reclaim_released()
    for row in _rows_since(ctx, before):
        assert "/proc" not in _row_str(row), f"/proc row during orphan sweep: {row}"
    assert not orphan_dir.exists()
    assert result is not None, "hygiene_reclaim_released returned None"
    assert str(orphan_dir) in result.removed_profiles, result
    assert ctx.reclaim_trash.is_dir()
    rd_after = reg._store.load_active_read()
    assert "t0-4b-keep" in rd_after.data


def test_t0_5_foreign_pid_not_signalled(g1_registry_isolation) -> None:
    ctx = g1_registry_isolation
    reg._store.write_active(
        {
            "act-1": {
                "status": "active",
                "chrome_pid": 424242,
                "holder": "t0-5",
                "purpose": "operator-proxy",
                "port": 19105,
            }
        }
    )
    before = len(ctx.ledger)
    with contextlib.suppress(PermissionError):
        reg.hygiene_reclaim_released()
    for row in _rows_since(ctx, before):
        if row.get("kind") == "kill":
            pid = row.get("pid") or (row.get("detail") or {}).get("pid")
            assert pid in (0, None, *_SPAWNED), f"os.kill foreign pid {row}"



def test_t0_6_dormant_paths_confined(g1_registry_isolation, tmp_path: Path) -> None:
    ctx = g1_registry_isolation
    before = len(ctx.ledger)
    row = reg.register_lane(
        holder="t0-6",
        purpose="operator-proxy",
        launch_chrome=_t0_6_launch,
        is_listening=lambda _p: False,
    )
    assert row.port in G1_TEST_PORT_RANGE
    assert row.purpose == "operator-proxy"
    assert reg.bind_chat_url_only(row.registration_id, chat_url=T0_6_CHAT_URL) is True
    rd = reg._store.load_active_read()
    rid = row.registration_id
    assert rd.present
    assert rd.data[rid].get("status") == "active"
    assert rd.data[rid].get("chat_url") == T0_6_CHAT_URL
    seat = reg.make_dormant(row.registration_id, reason="idle_exit", is_listening=lambda _p: True)
    assert seat is not None, "make_dormant refused"
    rd = reg._store.load_active_read()
    assert rd.data[rid]["status"] == "dormant"
    assert rd.data[rid]["dormant_reason"] == "idle_exit"
    assert rd.data[rid].get("chrome_pid") is None
    row2 = reg.relaunch_dormant(
        row.registration_id,
        holder="t0-6",
        launch_chrome=_t0_6_launch,
        is_listening=lambda _p: False,
    )
    assert row2.port in G1_TEST_PORT_RANGE
    rd = reg._store.load_active_read()
    assert rd.data[rid]["status"] == "active"
    assert rd.data[rid].get("seat_lane") in (None, "")
    seed_profile = tmp_path / "t0-6-seed-profile"
    seed_profile.mkdir()
    seed_rid = "seed-released-6"
    assert not ctx.reclaim_trash.is_dir()
    reg._store.write_active(
        {
            rid: rd.data[rid],
            seed_rid: {
                "status": "released",
                "profile": str(seed_profile),
                "profile_suffix": "t0-6-seed",
                "port": 19106,
                "holder": "t0-6-seed",
                "purpose": "operator-proxy",
            },
        }
    )
    with contextlib.suppress(PermissionError):
        reg.hygiene_reclaim_released()
    logs6 = reg._store.read_registry_log()
    hits6 = [
        e
        for e in logs6
        if e.get("event") == "hygiene_reclaim"
        and e.get("registration_id") == seed_rid
        and e.get("reclaim_outcome") == "success"
        and e.get("profile_removed") is True
    ]
    assert hits6, f"expected hygiene_reclaim receipt for {seed_rid}, got {logs6[-5:]}"
    assert not seed_profile.exists()
    assert ctx.reclaim_trash.is_dir()
    leg_tmp = next(p for p in tmp_path.parents if (p / "logs").is_dir())
    escape = leg_tmp / "home" / ".gateway" / ".reclaim-trash"
    assert not escape.exists(), f"escape destination {escape}"
    rd = reg._store.load_active_read()
    end = rd.data.get(rid, {})
    end_state = end.get("status")
    dormant_reason = end.get("dormant_reason")
    noop_pid = T0_6_NOOP_PID
    if end_state == "dormant":
        assert dormant_reason == "chrome_dead_reap"
    else:
        assert end_state == "active"
    print(
        f"T0.6-OBSERVED end_state={end_state} dormant_reason={dormant_reason} noop_pid={noop_pid}"
    )
    for path in (ctx.registry_root / "active.json", ctx.registry_root / "ports.lock"):
        assert path.exists(), f"missing registry artifact at {path}"
    permitted_spawn = {r.get("pid") for r in ctx.ledger if r.get("kind") == "spawn" and r.get("permitted")}
    bad: list[dict] = []
    for brow in _rows_since(ctx, before):
        if "/proc" in _row_str(brow):
            bad.append(brow)
            continue
        if brow.get("kind") == "spawn":
            bad.append(brow)
            continue
        if brow.get("kind") == "kill":
            pid = brow.get("pid") or (brow.get("detail") or {}).get("pid")
            if pid == T0_6_NOOP_PID:
                bad.append(brow)
            elif pid not in (None, 0) and pid not in permitted_spawn:
                bad.append(brow)
            continue
        if brow.get("kind") == "connect":
            fam = brow.get("family")
            if fam == "AF_UNIX":
                addr = brow.get("address", "")
                if isinstance(addr, str) and str(tmp_path) not in addr:
                    bad.append(brow)
            else:
                addr = brow.get("address")
                if isinstance(addr, tuple) and len(addr) == 2:
                    if addr[1] not in G1_TEST_PORT_RANGE:
                        bad.append(brow)
                elif fam is None:
                    bad.append(brow)
    assert not bad, f"composition rows outside tmp_path: {bad}"


def test_t0_7_no_live_event_socket(g1_registry_isolation) -> None:
    ctx = g1_registry_isolation
    before = len(ctx.ledger)
    reg.register_lane(
        holder="t0-7",
        purpose="operator-proxy",
        launch_chrome=_noop_launch,
        is_listening=lambda _p: False,
    )
    for row in _rows_since(ctx, before, kinds=("connect",)):
        assert "universal-protocol/events.sock" not in _row_str(row), f"live event socket: {row}"
        addr = row.get("address")
        if isinstance(addr, str):
            assert str(ctx.tmp_path) in addr, row


def test_t0_8_no_subprocess_ss(g1_registry_isolation) -> None:
    ctx = g1_registry_isolation
    before = len(ctx.ledger)
    row = reg.register_lane(
        holder="t0-8",
        purpose="operator-proxy",
        launch_chrome=_noop_launch,
        is_listening=lambda _p: False,
    )
    reg.deregister_lane(row.registration_id, kill=True, is_listening=lambda _p: True)
    spawns = [r for r in _rows_since(ctx, before) if r.get("kind") == "spawn"]
    assert not spawns, f"subprocess spawn rows: {spawns}"
