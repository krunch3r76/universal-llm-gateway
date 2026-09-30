"""bind_execution_lane routes operator-proxy non-hop births through ensure."""

from __future__ import annotations

import asyncio
import json
import threading
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from claude_bundles import cdp_registry as reg
from claude_bundles.cdp_lane import LaneError
from claude_bundles.project_ask import ProjectAskResult

from cdp_ask.models import SubmitProjectAskRequest
from cdp_ask.runner import bind_execution_lane, run_execution

pytestmark = pytest.mark.offline


def test_bind_execution_lane_operator_proxy_uses_ensure() -> None:
    req = SubmitProjectAskRequest(
        prompt_text="x",
        holder="op",
        purpose="operator-proxy",
        mission_kind="root",
        parent_thread="9497",
    )
    seat = MagicMock()
    seat.registration_id = "driving-root"
    with (
        patch(
            "cdp_ask.runner.ensure_driving_operator_seat",
            return_value=seat,
        ) as ensure,
        patch("cdp_ask.runner.cdp_registry.register_lane") as mint,
        patch(
            "claude_bundles.cdp_registry.session_address.retire_predecessor_identity",
        ) as retire,
        patch(
            "claude_bundles.cdp_registry_store.load_active",
            return_value={},
        ),
    ):
        out = bind_execution_lane(req, holder="op")
    assert out is seat
    ensure.assert_called_once_with(
        holder="op",
        parent_thread="9497",
        purpose="operator-proxy",
        mission_kind="root",
        joined=[],
    )
    mint.assert_not_called()
    retire.assert_called_once_with("driving-root", parent_thread="9497")


def test_bind_execution_lane_hop_still_mints() -> None:
    req = SubmitProjectAskRequest(
        prompt_text="x",
        holder="hop",
        purpose="operator-proxy",
        mission_kind="hop",
        parent_thread="9497",
    )
    minted = MagicMock()
    minted.registration_id = "hop-row"
    with (
        patch("cdp_ask.runner.ensure_driving_operator_seat") as ensure,
        patch(
            "cdp_ask.runner.cdp_registry.register_lane",
            return_value=minted,
        ) as mint,
        patch(
            "claude_bundles.cdp_registry.session_address.retire_predecessor_identity",
        ) as retire,
        patch(
            "claude_bundles.cdp_registry_store.load_active",
            return_value={},
        ),
    ):
        out = bind_execution_lane(req, holder="hop")
    assert out is minted
    ensure.assert_not_called()
    mint.assert_called_once()
    retire.assert_called_once_with("hop-row", parent_thread="9497")


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
    monkeypatch.setattr(reg.cdp_lane, "is_listening", lambda _port: False)
    monkeypatch.setenv("CDP_REGISTRY_SEAT_AUTHORITY", "1")
    monkeypatch.setattr(
        "claude_bundles.x_display_capacity.require_chrome_headroom",
        lambda **_kwargs: {"x_exhausted": False},
    )
    return root


def _parent_request(parent: str) -> SubmitProjectAskRequest:
    return SubmitProjectAskRequest(
        prompt_text="x",
        holder="op",
        purpose="operator-proxy",
        mission_kind="root",
        parent_thread=parent,
    )


def _allocating_ids(root: Path, parent: str) -> list[str]:
    path = root / "registry.jsonl"
    if not path.is_file():
        return []
    found: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        record = json.loads(line)
        if (
            record.get("event") == "allocating"
            and record.get("parent_thread") == parent
        ):
            found.append(str(record["registration_id"]))
    return found


def _spy_seat_mutations(monkeypatch: pytest.MonkeyPatch) -> tuple[list[int], list[int]]:
    bind_threads: list[int] = []
    retire_threads: list[int] = []
    real_bind = reg.bind_driving_seat
    from claude_bundles.cdp_registry.session_address import (
        retire_predecessor_identity as real_retire,
    )

    def spy_bind(registration_id: str) -> None:
        bind_threads.append(threading.get_ident())
        real_bind(registration_id)

    def spy_retire(
        registration_id: str, *, parent_thread: str | None = None
    ) -> list[str]:
        retire_threads.append(threading.get_ident())
        return real_retire(registration_id, parent_thread=parent_thread)

    monkeypatch.setattr(reg, "bind_driving_seat", spy_bind)
    monkeypatch.setattr(
        "claude_bundles.cdp_registry.session_address.retire_predecessor_identity",
        spy_retire,
    )
    return bind_threads, retire_threads


def _signal_when_others_see_allocating(
    monkeypatch: pytest.MonkeyPatch,
    launch_threads: list[int],
    entered: threading.Event,
) -> tuple[threading.Event, list[int]]:
    """Set an event once a non-launcher reloads an allocating row."""
    seen = threading.Event()
    seers: list[int] = []
    guard = threading.Lock()
    real = reg._store.load_active

    def wrapped() -> dict[str, dict[str, object]]:
        data = real()
        if (
            entered.is_set()
            and launch_threads
            and threading.get_ident() != launch_threads[0]
            and any(
                isinstance(row, dict) and row.get("status") == "allocating"
                for row in data.values()
            )
        ):
            with guard:
                if threading.get_ident() not in seers:
                    seers.append(threading.get_ident())
            seen.set()
        return data

    monkeypatch.setattr(reg._store, "load_active", wrapped)
    return seen, seers


def _seed_allocating(
    *,
    registration_id: str,
    parent: str,
    started_at: float,
    port: int,
) -> None:
    suffix = f"reg-{registration_id[:8]}"
    row = {
        "registration_id": registration_id,
        "port": port,
        "profile_suffix": suffix,
        "profile": str(reg.cdp_lane.profile_for(suffix)),
        "holder": "seed",
        "purpose": "operator-proxy",
        "display": ":2",
        "mission_kind": "root",
        "parent_thread": parent,
        "status": "allocating",
        "chrome_pid": None,
        "holder_pid": 1,
        "started_at": started_at,
    }
    active = reg._store.load_active()
    active[registration_id] = row
    reg._store.write_active(active)
    reg._HELD_LOCKS.pop(registration_id, None)


def test_two_threads_join_one_launch(
    isolated_registry: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    parent = "parent-join"
    entered = threading.Event()
    release = threading.Event()
    launch_threads: list[int] = []
    bind_threads, retire_threads = _spy_seat_mutations(monkeypatch)

    def launch(port: int, profile: Path) -> int:
        launch_threads.append(threading.get_ident())
        profile.mkdir(parents=True, exist_ok=True)
        entered.set()
        assert release.wait(timeout=5)
        return 1

    monkeypatch.setattr(reg.cdp_lane, "_launch_chrome", launch)
    loser_waiting, _seers = _signal_when_others_see_allocating(
        monkeypatch, launch_threads, entered
    )
    results: list[tuple[int, str | None, str | None, BaseException | None]] = []

    def run() -> None:
        ident = threading.get_ident()
        try:
            seat = bind_execution_lane(_parent_request(parent), holder="op")
            status = reg._load_active()[seat.registration_id]["status"]
            results.append((ident, seat.registration_id, status, None))
        except BaseException as exc:
            results.append((ident, None, None, exc))

    threads = [threading.Thread(target=run, daemon=True) for _ in range(2)]
    for thread in threads:
        thread.start()
    assert entered.wait(timeout=5)
    assert loser_waiting.wait(timeout=5)
    assert len(launch_threads) == 1
    assert len(results) == 0
    release.set()
    for thread in threads:
        thread.join(timeout=5)
        assert not thread.is_alive()
    assert [item[3] for item in results] == [None, None]
    assert len(launch_threads) == 1
    ids = {item[1] for item in results}
    assert len(ids) == 1
    assert {item[2] for item in results} == {"active"}
    loser = next(item[0] for item in results if item[0] not in launch_threads)
    assert loser not in bind_threads
    assert loser not in retire_threads
    assert _allocating_ids(isolated_registry, parent) == [next(iter(ids))]


def test_winner_failure_lets_loser_mint(
    isolated_registry: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    parent = "parent-rollback"
    entered = threading.Event()
    proceed = threading.Event()
    launch_count = {"n": 0}
    winner_id: list[str] = []
    launch_threads: list[int] = []

    def launch(port: int, profile: Path) -> int:
        profile.mkdir(parents=True, exist_ok=True)
        launch_count["n"] += 1
        if launch_count["n"] == 1:
            active = reg._load_active()
            winner_id.append(next(iter(active)))
            launch_threads.append(threading.get_ident())
            entered.set()
            assert proceed.wait(timeout=5)
            raise RuntimeError("chrome fail")
        return 1

    monkeypatch.setattr(reg.cdp_lane, "_launch_chrome", launch)
    loser_waiting, _seers = _signal_when_others_see_allocating(
        monkeypatch, launch_threads, entered
    )
    results: list[tuple[str | None, BaseException | None]] = []

    def run() -> None:
        try:
            seat = bind_execution_lane(_parent_request(parent), holder="op")
            results.append((seat.registration_id, None))
        except BaseException as exc:
            results.append((None, exc))

    threads = [threading.Thread(target=run, daemon=True) for _ in range(2)]
    try:
        for thread in threads:
            thread.start()
        assert entered.wait(timeout=5)
        assert loser_waiting.wait(timeout=5)
        assert launch_count["n"] == 1
        proceed.set()
        for thread in threads:
            thread.join(timeout=5)
            assert not thread.is_alive()
    finally:
        proceed.set()
    assert launch_count["n"] == 2
    errors = [item[1] for item in results if item[1] is not None]
    oks = [item[0] for item in results if item[0] is not None]
    assert len(errors) == 1
    assert isinstance(errors[0], RuntimeError)
    assert oks == [oks[0]]
    assert oks[0] != winner_id[0]
    assert winner_id[0] not in reg._load_active()
    assert reg._load_active()[oks[0]]["status"] == "active"


def test_join_wait_outlasts_launch_wait(
    isolated_registry: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    parent = "parent-outlast"
    monkeypatch.setattr(reg.cdp_lane, "_LAUNCH_WAIT_S", 0.05)
    entered = threading.Event()
    entered_at: list[float] = []
    launch_threads: list[int] = []
    returned: list[tuple[str | None, BaseException | None]] = []

    def launch(port: int, profile: Path) -> int:
        profile.mkdir(parents=True, exist_ok=True)
        launch_threads.append(threading.get_ident())
        entered_at.append(time.monotonic())
        entered.set()
        time.sleep(0.4)
        return 1

    monkeypatch.setattr(reg.cdp_lane, "_launch_chrome", launch)
    loser_waiting, _seers = _signal_when_others_see_allocating(
        monkeypatch, launch_threads, entered
    )

    def run() -> None:
        try:
            seat = bind_execution_lane(_parent_request(parent), holder="op")
            returned.append((seat.registration_id, None))
        except BaseException as exc:
            returned.append((None, exc))

    threads = [threading.Thread(target=run, daemon=True) for _ in range(2)]
    for thread in threads:
        thread.start()
    assert entered.wait(timeout=5)
    assert loser_waiting.wait(timeout=5)
    elapsed = time.monotonic() - entered_at[0]
    if elapsed < 0.08:
        time.sleep(0.08 - elapsed)
    assert time.monotonic() - entered_at[0] >= 0.08
    assert returned == []
    for thread in threads:
        thread.join(timeout=5)
        assert not thread.is_alive()
    assert len(entered_at) == 1
    assert [item[1] for item in returned] == [None, None]
    assert not any(isinstance(item[1], LaneError) for item in returned)
    assert len({item[0] for item in returned}) == 1


def test_stranded_allocating_row_is_not_joined(
    isolated_registry: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    parent = "parent-stranded"
    launches: list[int] = []

    def launch(port: int, profile: Path) -> int:
        launches.append(port)
        profile.mkdir(parents=True, exist_ok=True)
        return 1

    monkeypatch.setattr(reg.cdp_lane, "_launch_chrome", launch)
    _seed_allocating(
        registration_id="stranded-row-0001",
        parent=parent,
        started_at=1.0,
        port=9223,
    )
    assert reg.is_driver_lock_held("stranded-row-0001") is False
    seat = bind_execution_lane(_parent_request(parent), holder="op")
    assert len(launches) == 1
    assert seat.registration_id != "stranded-row-0001"
    assert reg._load_active()["stranded-row-0001"]["status"] == "allocating"
    assert not isinstance(seat, LaneError)


def test_stranded_plus_live_winner_joins_winner(
    isolated_registry: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    parent = "parent-live"
    entered = threading.Event()
    release = threading.Event()
    launches: list[int] = []

    def launch(port: int, profile: Path) -> int:
        launches.append(threading.get_ident())
        profile.mkdir(parents=True, exist_ok=True)
        entered.set()
        assert release.wait(timeout=5)
        return 1

    monkeypatch.setattr(reg.cdp_lane, "_launch_chrome", launch)
    _seed_allocating(
        registration_id="stranded-older",
        parent=parent,
        started_at=1.0,
        port=9223,
    )
    winner: list[str] = []
    ask_ids: list[str] = []
    errors: list[BaseException] = []
    in_join_wait = threading.Event()
    from claude_bundles.cdp_registry import lifecycle as registry_lifecycle

    real_wait = registry_lifecycle._wait_for_joined_row

    def wrapped_wait(row: dict, **kwargs: object):
        in_join_wait.set()
        return real_wait(row, **kwargs)

    monkeypatch.setattr(registry_lifecycle, "_wait_for_joined_row", wrapped_wait)

    def run_winner() -> None:
        try:
            seat = bind_execution_lane(_parent_request(parent), holder="op")
            winner.append(seat.registration_id)
        except BaseException as exc:
            errors.append(exc)

    def run_ask() -> None:
        try:
            seat = bind_execution_lane(_parent_request(parent), holder="op-2")
            ask_ids.append(seat.registration_id)
        except BaseException as exc:
            errors.append(exc)

    winner_thread = threading.Thread(target=run_winner, daemon=True)
    ask_thread = threading.Thread(target=run_ask, daemon=True)
    try:
        winner_thread.start()
        assert entered.wait(timeout=5)
        ask_thread.start()
        assert in_join_wait.wait(timeout=5)
        assert len(launches) == 1
        release.set()
        winner_thread.join(timeout=5)
        ask_thread.join(timeout=5)
        assert not winner_thread.is_alive()
        assert not ask_thread.is_alive()
    finally:
        release.set()
    assert errors == []
    assert winner == ask_ids
    assert ask_ids[0] != "stranded-older"
    assert len(launches) == 1
    assert reg._load_active()["stranded-older"]["status"] == "allocating"


def test_three_threads_winner_failure_launches_twice(
    isolated_registry: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    parent = "parent-three"
    entered = threading.Event()
    proceed = threading.Event()
    launches: list[int] = []
    launch_threads: list[int] = []

    def launch(port: int, profile: Path) -> int:
        profile.mkdir(parents=True, exist_ok=True)
        launches.append(1)
        if len(launches) == 1:
            launch_threads.append(threading.get_ident())
            entered.set()
            assert proceed.wait(timeout=5)
            raise RuntimeError("winner failed")
        return 1

    monkeypatch.setattr(reg.cdp_lane, "_launch_chrome", launch)
    _seen, seers = _signal_when_others_see_allocating(
        monkeypatch, launch_threads, entered
    )
    start = threading.Barrier(3)
    results: list[tuple[str | None, BaseException | None]] = []

    def run() -> None:
        start.wait(timeout=5)
        try:
            seat = bind_execution_lane(_parent_request(parent), holder="op")
            results.append((seat.registration_id, None))
        except BaseException as exc:
            results.append((None, exc))

    threads = [threading.Thread(target=run, daemon=True) for _ in range(3)]
    try:
        for thread in threads:
            thread.start()
        assert entered.wait(timeout=5)
        deadline = time.monotonic() + 5
        while len(seers) < 2 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert len(seers) >= 2
        assert len(launches) == 1
        proceed.set()
        for thread in threads:
            thread.join(timeout=5)
            assert not thread.is_alive()
    finally:
        proceed.set()
    assert len(launches) == 2
    errors = [item[1] for item in results if item[1] is not None]
    oks = [item[0] for item in results if item[0] is not None]
    assert len(errors) == 1
    assert isinstance(errors[0], RuntimeError)
    assert len(oks) == 2
    assert oks[0] == oks[1]


def test_rollback_between_wait_observations_launches_twice(
    isolated_registry: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    parent = "parent-between"
    entered = threading.Event()
    release = threading.Event()
    second_launch = threading.Event()
    launches: list[int] = []
    from claude_bundles.cdp_registry import lifecycle as registry_lifecycle

    def launch(port: int, profile: Path) -> int:
        profile.mkdir(parents=True, exist_ok=True)
        launches.append(port)
        if len(launches) == 1:
            entered.set()
            assert release.wait(timeout=5)
            return 1
        second_launch.set()
        return 1

    monkeypatch.setattr(reg.cdp_lane, "_launch_chrome", launch)
    barrier = threading.Barrier(2)
    gate = threading.Lock()
    winner_id: list[str] = []
    rolled = {"done": False}
    hook_finished = threading.Event()

    def hook(registration_id: str) -> None:
        with gate:
            if not winner_id:
                winner_id.append(registration_id)
            winner = winner_id[0]
        if registration_id != winner or hook_finished.is_set():
            return
        assert entered.wait(timeout=5)
        barrier.wait(timeout=5)
        with gate:
            if not rolled["done"]:
                registry_lifecycle._rollback_allocating(registration_id)
                rolled["done"] = True
        hook_finished.set()

    monkeypatch.setattr(registry_lifecycle, "_between_wait_observations", hook)
    start = threading.Barrier(3)
    results: list[BaseException | None] = []

    def run() -> None:
        start.wait(timeout=5)
        try:
            bind_execution_lane(_parent_request(parent), holder="op")
            results.append(None)
        except BaseException as exc:
            results.append(exc)

    threads = [threading.Thread(target=run, daemon=True) for _ in range(3)]
    try:
        for thread in threads:
            thread.start()
        assert hook_finished.wait(timeout=5)
        assert second_launch.wait(timeout=5)
    finally:
        release.set()
    for thread in threads:
        thread.join(timeout=5)
        assert not thread.is_alive()
    assert len(launches) == 2
    assert len(results) == 3


def test_second_reentry_raises_joined_reentry_exhausted(
    isolated_registry: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from claude_bundles.cdp_registry import lifecycle as registry_lifecycle

    reserve_calls: list[bool] = []

    def forbid_reserve(**_kwargs: object) -> tuple[dict[str, object], bool]:
        reserve_calls.append(True)
        raise AssertionError("reserve_allocating_row called")

    monkeypatch.setattr(registry_lifecycle, "reserve_allocating_row", forbid_reserve)
    with pytest.raises(reg.JoinedReentryExhausted):
        registry_lifecycle._wait_for_joined_row(
            {"registration_id": "missing-joined-row"},
            holder="op",
            purpose="operator-proxy",
            mission_kind="root",
            parent_thread="parent-reentry",
            listen=lambda _port: False,
            launch_fn=lambda _port, _profile: 1,
            joined=None,
            reentry=True,
        )
    assert reserve_calls == []


@pytest.mark.asyncio
async def test_run_execution_bind_yields_the_event_loop(
    isolated_registry: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    entered = threading.Event()
    release = threading.Event()

    def launch(port: int, profile: Path) -> int:
        profile.mkdir(parents=True, exist_ok=True)
        entered.set()
        assert release.wait(timeout=5)
        return 1

    monkeypatch.setattr(reg.cdp_lane, "_launch_chrome", launch)
    delta: dict[str, float] = {}

    async def watcher() -> None:
        while not entered.is_set():
            await asyncio.sleep(0)
        started = time.monotonic()
        await asyncio.sleep(0)
        delta["sleep0"] = time.monotonic() - started
        release.set()

    result = ProjectAskResult(
        ok=True,
        body="ok",
        url="",
        project_uuid="",
        project_url="",
        model={},
        body_len=2,
        delete_after=None,
    )
    try:
        with (
            patch(
                "cdp_ask.runner.run_project_conversation",
                new=AsyncMock(return_value=[result]),
            ),
            patch("cdp_ask.runner.registration_has_wake_debt", return_value=False),
        ):
            watch = asyncio.create_task(watcher())
            payload = await run_execution(
                SubmitProjectAskRequest(
                    prompt_text="ping",
                    converse=True,
                    no_project_uuid=True,
                    purpose="ask",
                    holder="op",
                ),
                execution_id="sat-bind",
                abort_check=AsyncMock(return_value=False),
            )
            await watch
    finally:
        release.set()
    assert payload["ok"] is True
    assert delta["sleep0"] < 1
