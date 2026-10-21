"""Hermetic tests for restart cadence arming and audit rows."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from scripts.model_manager.ui.controller import restart_cadence as cadence
from scripts.model_manager.ui.controller import restart_cadence_record as record
from scripts.model_manager.ui.controller.restart_intent_store import Intent

pytestmark = pytest.mark.offline

_MASTER = "a" * 40
_SEALED = "b" * 40


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _loop(
    *,
    rev_parse: cadence.RevParseFn | None = None,
    diff_name_only: cadence.DiffFn | None = None,
    probe_service: cadence.ProbeFn | None = None,
    pending_services: cadence.PendingFn | None = None,
    execute: cadence.ExecuteFn | None = None,
    db_path: Path,
) -> cadence.RestartCadenceLoop:
    controller = MagicMock()
    controller.root = Path("/repo")
    return cadence.RestartCadenceLoop(
        controller,
        rev_parse=rev_parse,
        diff_name_only=diff_name_only,
        probe_service=probe_service,
        pending_services=pending_services,
        execute=execute,
        db_path=db_path,
    )


def test_service_down_probe_none_skips_execute(tmp_path: Path) -> None:
    """Break: stopped service — cadence must not call sync_restart."""
    calls: list[str] = []

    async def _execute(*_a: Any, **_k: Any) -> dict[str, Any]:
        calls.append("execute")
        return {}

    loop = _loop(
        rev_parse=lambda ref: _MASTER if ref == "master" else _SEALED,
        diff_name_only=lambda _o, _n: ["services/universal-stargate/foo.py"],
        probe_service=lambda _s: None,
        pending_services=lambda: frozenset(),
        execute=_execute,
        db_path=tmp_path / "cadence.db",
    )
    _run(loop._tick_service("stargate", _MASTER))
    assert calls == []


def test_rev_parse_failure_skips_execute(tmp_path: Path) -> None:
    """Break: unreadable sealed sha — no arm."""
    calls: list[str] = []

    async def _execute(*_a: Any, **_k: Any) -> dict[str, Any]:
        calls.append("execute")
        return {}

    loop = _loop(
        rev_parse=lambda ref: None,
        probe_service=lambda _s: {
            "status": "running",
            "pid": 10,
            "code_version": "deadbeef",
        },
        pending_services=lambda: frozenset(),
        execute=_execute,
        db_path=tmp_path / "cadence.db",
    )
    _run(loop._tick_service("stargate", _MASTER))
    assert calls == []


def test_diff_only_docs_skips_execute(tmp_path: Path) -> None:
    """Break: master drift in docs only — load surface empty for stargate."""
    calls: list[str] = []

    async def _execute(*_a: Any, **_k: Any) -> dict[str, Any]:
        calls.append("execute")
        return {}

    loop = _loop(
        rev_parse=lambda ref: _MASTER if ref == "master" else _SEALED,
        diff_name_only=lambda _o, _n: ["docs/readme.md", "libs/agent_bus_store/x.py"],
        probe_service=lambda _s: {
            "status": "running",
            "pid": 11,
            "code_version": _SEALED,
        },
        pending_services=lambda: frozenset(),
        execute=_execute,
        db_path=tmp_path / "cadence.db",
    )
    _run(loop._tick_service("stargate", _MASTER))
    assert calls == []


def test_load_surface_diff_arms_sync_restart(tmp_path: Path) -> None:
    """Break: stargate-owned .py on master — one non-force arm with master code_ref."""
    captured: list[dict[str, Any]] = []

    async def _execute(_ctl: Any, method: str, service: str, params: dict[str, Any]) -> dict[str, Any]:
        captured.append({"method": method, "service": service, "params": dict(params)})
        return {"status": "deferred", "restart_intent_id": "intent-1"}

    loop = _loop(
        rev_parse=lambda ref: _MASTER if ref == "master" else _SEALED,
        diff_name_only=lambda _o, _n: ["services/universal-stargate/systems/proxy/x.py"],
        probe_service=lambda _s: {
            "status": "running",
            "pid": 12,
            "code_version": _SEALED,
        },
        pending_services=lambda: frozenset(),
        execute=_execute,
        db_path=tmp_path / "cadence.db",
    )
    _run(loop._tick_service("stargate", _MASTER))
    assert len(captured) == 1
    assert captured[0]["method"] == "sync_restart"
    assert captured[0]["service"] == "stargate"
    assert captured[0]["params"]["force"] is False
    assert captured[0]["params"]["code_ref"] == _MASTER
    assert captured[0]["params"]["caller_agent"] == "restart_cadence"
    assert "review" not in captured[0]["params"]


def test_equal_sealed_and_master_skips_execute(tmp_path: Path) -> None:
    """Break: process already on master seal — no arm."""
    calls: list[str] = []

    async def _execute(*_a: Any, **_k: Any) -> dict[str, Any]:
        calls.append("execute")
        return {}

    same = "c" * 40
    loop = _loop(
        rev_parse=lambda _ref: same,
        diff_name_only=lambda _o, _n: ["services/universal-stargate/x.py"],
        probe_service=lambda _s: {
            "status": "running",
            "pid": 13,
            "code_version": same,
        },
        pending_services=lambda: frozenset(),
        execute=_execute,
        db_path=tmp_path / "cadence.db",
    )
    _run(loop._tick_service("stargate", same))
    assert calls == []


def test_pending_intent_skips_execute(tmp_path: Path) -> None:
    """Break: pending restart intent — cadence must not double-arm."""
    calls: list[str] = []

    async def _execute(*_a: Any, **_k: Any) -> dict[str, Any]:
        calls.append("execute")
        return {}

    loop = _loop(
        rev_parse=lambda ref: _MASTER if ref == "master" else _SEALED,
        diff_name_only=lambda _o, _n: ["services/universal-stargate/x.py"],
        probe_service=lambda _s: {
            "status": "running",
            "pid": 14,
            "code_version": _SEALED,
        },
        pending_services=lambda: frozenset({"stargate"}),
        execute=_execute,
        db_path=tmp_path / "cadence.db",
    )
    _run(loop._tick())
    assert calls == []


def test_one_execute_exception_still_arms_next_service(tmp_path: Path) -> None:
    """Break: first slug raises — later slug still arms."""
    seen: list[str] = []

    async def _execute(_ctl: Any, _method: str, service: str, _params: dict[str, Any]) -> dict[str, Any]:
        seen.append(service)
        if service == "agent_bus":
            raise RuntimeError("probe wedge")
        return {"status": "deferred", "restart_intent_id": f"id-{service}"}

    loop = _loop(
        rev_parse=lambda ref: _MASTER if ref == "master" else _SEALED,
        diff_name_only=lambda _o, _n: [
            "services/agent-bus/x.py",
            "libs/cortex_store/server.py",
        ],
        probe_service=lambda s: {
            "status": "running",
            "pid": 20,
            "code_version": _SEALED,
        },
        pending_services=lambda: frozenset(),
        execute=_execute,
        db_path=tmp_path / "cadence.db",
    )

    async def _tick_subset() -> None:
        for service in ("agent_bus", "cortex_api"):
            try:
                await loop._tick_service(service, _MASTER)
            except RuntimeError:
                pass

    _run(_tick_subset())
    assert "agent_bus" in seen
    assert "cortex_api" in seen


def test_deferred_intent_writes_pending_row(tmp_path: Path) -> None:
    """Break: deferred drain — row captures old_pid and code_ref with new_pid NULL."""
    db = tmp_path / "cadence.db"

    async def _execute(*_a: Any, **_k: Any) -> dict[str, Any]:
        return {"status": "deferred", "restart_intent_id": "cadence-intent-8"}

    loop = _loop(
        rev_parse=lambda ref: _MASTER if ref == "master" else _SEALED,
        diff_name_only=lambda _o, _n: ["services/universal-stargate/x.py"],
        probe_service=lambda _s: {
            "status": "running",
            "pid": 88,
            "code_version": _SEALED,
        },
        pending_services=lambda: frozenset(),
        execute=_execute,
        db_path=db,
    )
    _run(loop._tick_service("stargate", _MASTER))
    row = record.get_record("cadence-intent-8", db_path=db)
    assert row is not None
    assert row.old_pid == 88
    assert row.code_ref == _MASTER
    assert row.new_pid is None
    assert row.recorded_at is None


def test_reconcile_fills_or_deletes_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Break: terminal intent projection drives fill vs delete on pending rows."""
    db = tmp_path / "cadence.db"
    record.insert_armed(
        intent_id="fill-me",
        service="stargate",
        old_pid=1,
        code_ref=_MASTER,
        db_path=db,
    )
    record.insert_armed(
        intent_id="delete-me",
        service="stargate",
        old_pid=2,
        code_ref=_MASTER,
        db_path=db,
    )
    record.insert_armed(
        intent_id="same-pid",
        service="stargate",
        old_pid=99,
        code_ref=_MASTER,
        db_path=db,
    )

    def _intent(intent_id: str) -> Intent:
        return Intent(
            intent_id=intent_id,
            service="stargate",
            action="sync_restart",
            status="completed",
            drain_epoch=1,
            worker_id=None,
            worker_started_at=None,
            deadline_at=None,
            last_seen_event_seq=0,
            reason="test",
            kill_boundary_at=None,
            created_at="t0",
            updated_at="t1",
        )

    monkeypatch.setattr(record, "load_intent", _intent)

    projections = {
        "fill-me": {"executed": True},
        "delete-me": {"executed": False},
        "same-pid": {"executed": True},
    }

    def _project(_intent: Intent) -> dict[str, Any] | None:
        return projections.get(_intent.intent_id)

    loop = _loop(
        probe_service=lambda _s: {"status": "running", "pid": 99, "code_version": _SEALED},
        db_path=db,
    )

    # Import inside reconcile uses restart_intent_consumer — patch there too.
    import scripts.model_manager.ui.controller.restart_intent_consumer as ric

    monkeypatch.setattr(ric, "project_restart_intent_last", _project)

    loop._reconcile_pending_records()

    filled = record.get_record("fill-me", db_path=db)
    assert filled is not None
    assert filled.new_pid == 99
    assert filled.recorded_at is not None

    assert record.get_record("delete-me", db_path=db) is None

    unchanged = record.get_record("same-pid", db_path=db)
    assert unchanged is not None
    assert unchanged.new_pid is None


def test_record_module_has_no_agent_bus_import() -> None:
    """Break: record layer must not pull agent-bus thread wiring."""
    text = Path(record.__file__).read_text(encoding="utf-8")
    assert "agent_bus" not in text
    assert "15851" not in text


def test_cadence_never_sets_force_true_in_source() -> None:
    """Break: cadence must not pass force True into execute."""
    text = Path(cadence.__file__).read_text(encoding="utf-8")
    assert '"force": True' not in text
    assert "'force': True" not in text


def test_restart_cadence_interval_is_fifteen_minutes() -> None:
    """Break: arm bound must stay at 900s."""
    assert cadence.RESTART_CADENCE_INTERVAL_S == 900.0


def test_lib_path_serving_includes_service() -> None:
    """Break: shared lib edit must map to serving slug for filter."""
    path = "libs/cortex_store/server.py"
    assert cadence.path_affects_service_load_surface(path, "cortex_api")
    assert not cadence.path_affects_service_load_surface(path, "stargate")
