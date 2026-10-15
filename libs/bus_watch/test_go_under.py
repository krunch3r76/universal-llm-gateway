"""``go under`` verb — state flip, seat release, ticker guarantee, handoff wake."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

from bus_watch.digest_budget import effective_policy
from bus_watch.go_under import _HUB_ROOT, go_under, start_ticker, under_line
from bus_watch.spawn_pending import handoff_wake, record_spawn_service

_REPO = Path(__file__).resolve().parents[2]


def _state() -> dict:
    return {
        "register": "attended",
        "ticks": 485,
        "policy": {
            "gear": "3-wake-on-attention",
            "ready": False,
            "successor_model": "cursor/composer-2.5",
        },
    }


def test_go_under_arms_and_frees_the_seat(tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
    holder = "ide:f0fbd8f2-305a-48e8-8c61-1004ecfff015"
    monkeypatch.setattr(
        "bus_watch.go_under.read_lock", lambda *_a, **_k: {"holder": holder}
    )
    released: list[tuple] = []
    state = _state()
    path = tmp_path / "liaison-10534.tick.json"
    result = go_under(
        "10534",
        state,
        state_path=path,
        holder=holder,
        stop_loops=lambda root: [4242],
        ensure=lambda root: {"alive": False, "started": True, "pid": 1},
        release=lambda h, **kw: released.append((h, kw)) or {"ok": True},
        mint=lambda *_a, **_k: None,
    )
    assert result["ok"] is True and result["armed"] is True
    assert state["register"] == "autonomous"
    assert state["policy"]["ready"] is True
    assert result["ready"] == {"before": False, "after": True}
    assert (
        state["handoff"]["seq"] == 1 and state["handoff"]["from_register"] == "attended"
    )
    assert released == [(holder, {"pid": None, "root_id": "10534"})]
    assert result["stopped_loops"] == [4242] and result["ticker"]["started"] is True
    assert json.loads(path.read_text())["handoff"]["seq"] == 1
    assert effective_policy(state)["ready"] is True
    assert handoff_wake(state) is True
    assert result["under_line"] == under_line("10534", effective_policy(state))
    assert "successor_model=cursor/composer-2.5" in result["under_line"]


def test_go_under_plants_gear_three_on_fresh_policy(
    tmp_path: Path, monkeypatch
) -> None:  # noqa: ANN001
    """Go-under on a root that never set gear must enable wake + digest."""
    monkeypatch.setattr("bus_watch.go_under.read_lock", lambda *_a, **_k: {})
    state = {
        "register": "attended",
        "policy": {"successor_model": "cdp/opus-5"},
    }
    result = go_under(
        "11667",
        state,
        state_path=tmp_path / "s.json",
        stop_loops=lambda root: [],
        ensure=lambda root: {"alive": True, "started": False},
        release=lambda *_a, **_k: {"ok": True},
        mint=lambda *_a, **_k: None,
    )
    assert result["ok"] is True
    assert state["policy"]["gear"] == "3-wake-on-attention"
    assert state["policy"]["successor_seat"] == "cdp"
    eff = effective_policy(state)
    assert eff["wake_on_attention_only"] is True
    assert eff["post_digest"] is True
    assert eff["successor_model"] == "cdp/opus-5"
    assert eff["successor_seat"] == "cdp"


def test_go_under_leaves_sdk_holder_and_refuses_unbound_model(
    tmp_path: Path, monkeypatch
) -> None:  # noqa: ANN001
    monkeypatch.setattr(
        "bus_watch.go_under.read_lock", lambda *_a, **_k: {"holder": "sdk:abc123"}
    )
    state = _state()
    # Gear-3 preset now supplies grok. Only an explicit empty override is unset.
    state["policy"]["successor_model"] = ""
    result = go_under(
        "10479",
        state,
        state_path=tmp_path / "s.json",
        stop_loops=lambda root: [],
        ensure=lambda root: {"alive": True, "started": False},
        release=lambda *_a, **_k: {"ok": True},
        mint=lambda *_a, **_k: None,
    )
    assert result["seat_release"]["reason"] == "not_ide_holder"
    assert result["ok"] is False and "successor_model_unset" in result["refused"]
    assert state["register"] == "autonomous"


def test_go_under_sets_loop_thread(tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr("bus_watch.go_under.read_lock", lambda *_a, **_k: {})
    mint_calls: list[tuple] = []

    def fake_mint(root: str, state: dict, state_path: Path) -> str:
        mint_calls.append((root, state_path))
        state.setdefault("policy", {})["loop_thread"] = "12022"
        return "12022"

    state = _state()
    go_under(
        "11960",
        state,
        state_path=tmp_path / "s.json",
        stop_loops=lambda root: [],
        ensure=lambda root: {"alive": True},
        release=lambda *_a, **_k: {"ok": True},
        mint=fake_mint,
    )
    assert mint_calls == [("11960", tmp_path / "s.json")]
    assert state["policy"]["loop_thread"] == "12022"
    saved = json.loads((tmp_path / "s.json").read_text())
    assert saved["policy"]["loop_thread"] == "12022"


def test_second_go_under_does_not_remint(tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr("bus_watch.go_under.read_lock", lambda *_a, **_k: {})
    mint_calls = 0

    def counting_mint(root: str, state: dict, state_path: Path) -> str:
        nonlocal mint_calls
        mint_calls += 1
        existing = str((state.get("policy") or {}).get("loop_thread") or "")
        if existing:
            return existing
        state.setdefault("policy", {})["loop_thread"] = "12022"
        return "12022"

    state = {**_state(), "policy": {**_state()["policy"], "loop_thread": "12022"}}
    go_under(
        "11960",
        state,
        state_path=tmp_path / "s.json",
        stop_loops=lambda root: [],
        ensure=lambda root: {"alive": True},
        release=lambda *_a, **_k: {"ok": True},
        mint=counting_mint,
    )
    assert mint_calls == 1
    assert state["policy"]["loop_thread"] == "12022"


def test_second_go_under_bumps_seq_and_spawn_latches_it(
    tmp_path: Path, monkeypatch
) -> None:  # noqa: ANN001
    monkeypatch.setattr("bus_watch.go_under.read_lock", lambda *_a, **_k: {})
    state = {**_state(), "handoff": {"seq": 3}, "handoff_spawned_seq": 3}
    assert handoff_wake(state) is False
    go_under(
        "10534",
        state,
        state_path=tmp_path / "s.json",
        stop_loops=lambda root: [],
        ensure=lambda root: {"alive": True},
        release=lambda *_a, **_k: {"ok": True},
        mint=lambda *_a, **_k: None,
    )
    assert state["handoff"]["seq"] == 4 and handoff_wake(state) is True
    state["pending_spawn"] = {"thread_id": "10601", "execution_id": "x"}
    record_spawn_service(
        state,
        [{"id": "10593", "turns": 2, "status": "closed", "lifecycle": "completed"}],
    )
    assert handoff_wake(state) is False
    assert state["served_closeouts"] == {"10593": 2}
    assert state["successor_threads"] == ["10601"]


def test_start_ticker_uses_systemctl_start_when_unit_loaded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []

    def fake_run(cmd: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(list(cmd))
        joined = " ".join(cmd)
        if "Version" in joined:
            return subprocess.CompletedProcess(cmd, 0, stdout="255\n", stderr="")
        if "LoadState" in joined:
            return subprocess.CompletedProcess(cmd, 0, stdout="loaded\n", stderr="")
        if cmd[:3] == ["systemctl", "--user", "start"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        raise AssertionError(f"unexpected cmd: {cmd!r}")

    monkeypatch.setattr("bus_watch.go_under.subprocess.run", fake_run)
    monkeypatch.setattr(
        "bus_watch.go_under.subprocess.Popen",
        lambda *_a, **_k: pytest.fail("Popen must not run"),
    )
    out = start_ticker("10534")
    assert out == {
        "started": True,
        "pid": None,
        "log": None,
        "supervisor": "systemd",
        "unit": "liaison-ticker-10534.service",
    }
    assert any(c[:3] == ["systemctl", "--user", "start"] for c in calls)
    assert not any(c and c[0] == "systemd-run" for c in calls)


def test_start_ticker_systemd_run_when_unit_not_loaded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []

    def fake_run(cmd: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(list(cmd))
        joined = " ".join(cmd)
        if "Version" in joined:
            return subprocess.CompletedProcess(cmd, 0, stdout="255\n", stderr="")
        if "LoadState" in joined:
            return subprocess.CompletedProcess(cmd, 0, stdout="\n", stderr="")
        if cmd and cmd[0] == "systemd-run":
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        raise AssertionError(f"unexpected cmd: {cmd!r}")

    monkeypatch.setattr("bus_watch.go_under.subprocess.run", fake_run)
    out = start_ticker("16401")
    assert out["unit"] == "liaison-ticker-16401.service"
    run_cmd = next(c for c in calls if c and c[0] == "systemd-run")
    joined = " ".join(run_cmd)
    assert str(_HUB_ROOT) in joined
    assert f"{_HUB_ROOT}/libs" in joined
    assert "--root 16401" in joined or "16401" in run_cmd
    assert "liaison-tick.py" in joined
    worktree = str(_REPO)
    assert worktree not in joined or worktree == str(_HUB_ROOT)


def test_start_ticker_refuses_when_user_manager_unreachable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(cmd: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="Failed to connect to bus")

    popen_calls: list[object] = []
    monkeypatch.setattr("bus_watch.go_under.subprocess.run", fake_run)
    monkeypatch.setattr(
        "bus_watch.go_under.subprocess.Popen",
        lambda *a, **k: popen_calls.append((a, k)) or pytest.fail("Popen"),
    )
    out = start_ticker("10534")
    assert out == {
        "started": False,
        "pid": None,
        "supervisor": None,
        "reason": "user_manager_unreachable",
    }
    assert not popen_calls


def test_go_under_ok_false_when_ticker_start_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("bus_watch.go_under.read_lock", lambda *_a, **_k: {})
    result = go_under(
        "10534",
        _state(),
        state_path=tmp_path / "s.json",
        stop_loops=lambda root: [],
        ensure=lambda root: {
            "started": False,
            "pid": None,
            "supervisor": None,
            "reason": "user_manager_unreachable",
        },
        release=lambda *_a, **_k: {"ok": True},
        mint=lambda *_a, **_k: None,
    )
    assert result["ok"] is False
    assert result["refused"] == "user_manager_unreachable"


def test_liaison_tick_go_under_exits_3_on_refused_ticker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state_path = tmp_path / "liaison-10534.tick.json"
    state_path.write_text(
        json.dumps(
            {
                "register": "attended",
                "policy": {
                    "gear": "3-wake-on-attention",
                    "ready": False,
                    "successor_model": "cursor/composer-2.5",
                },
            }
        ),
        encoding="utf-8",
    )

    def fake_go_under(*_a, **_k):  # noqa: ANN002, ANN003
        return {
            "ok": False,
            "refused": "user_manager_unreachable",
            "under_line": "UNDER → liaison-ticker-10534",
            "ticker": {
                "started": False,
                "reason": "user_manager_unreachable",
            },
        }

    spec = importlib.util.spec_from_file_location(
        "liaison_tick_go_under_exit",
        _REPO / "scripts" / "liaison-tick.py",
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["liaison_tick_go_under_exit"] = mod
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "go_under", fake_go_under)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "liaison-tick.py",
            "--root",
            "10534",
            "--go-under",
            "--state-file",
            str(state_path),
        ],
    )
    assert mod.main() == 3


def test_ticker_alive_false_when_holder_pid_dead(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from bus_watch import fable_lock as fl
    from bus_watch.go_under import ticker_alive

    monkeypatch.setattr(fl, "WATCH_DIR", tmp_path)
    fl.ticker_lock_path("10534").write_text(
        json.dumps(
            {"holder": "ticker:10534", "holder_pid": 999999, "root": "10534"}
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(fl, "_holder_pid_alive", lambda _pid: False)
    assert ticker_alive("10534") is False


def test_ticker_alive_false_wrong_holder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from bus_watch import fable_lock as fl
    from bus_watch.go_under import ticker_alive

    monkeypatch.setattr(fl, "WATCH_DIR", tmp_path)
    fl.ticker_lock_path("10534").write_text(
        json.dumps(
            {"holder": "ticker:99999", "holder_pid": 424242, "root": "10534"}
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(fl, "_holder_pid_alive", lambda _pid: True)
    assert ticker_alive("10534") is False
    assert fl.claim_ticker_lease("10534")["ok"] is True
