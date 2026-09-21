"""Fleet stop must not tear down peers while a drain barrier is pending.

Operator bind 2026-07-28: if any service prevents Sync+Restart All, all
services remain up until drain completes — then stop together.
"""

from __future__ import annotations

import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from scripts.model_manager.ui.controller.fleet_local_drain import drain_stop_git_worker
from scripts.model_manager.ui.model.service_state import ServiceStatus

_CTL = Path(__file__).resolve().parents[1]
_FLEET_PY = _CTL / "fleet.py"
_FLEET_LOCAL_PY = _CTL / "fleet_local.py"
_FLEET_DRAIN_PY = _CTL / "fleet_local_drain.py"


@pytest.mark.offline
def test_fleet_stop_awaits_drain_barrier_before_remote_taskgroup() -> None:
    """_stop_fleet_before_operation must call drain barrier before TaskGroup remotes."""
    source = _FLEET_PY.read_text(encoding="utf-8")
    tree = ast.parse(source)
    method = None
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "FleetOrchestrator":
            for item in node.body:
                if (
                    isinstance(item, ast.AsyncFunctionDef)
                    and item.name == "_stop_fleet_before_operation"
                ):
                    method = item
                    break
    assert method is not None, "_stop_fleet_before_operation missing"
    body_src = ast.get_source_segment(source, method) or ""
    barrier_at = body_src.find("drain_local_barriers_before_stop")
    taskgroup_at = body_src.find("asyncio.TaskGroup")
    assert barrier_at >= 0, "drain barrier call missing"
    assert taskgroup_at >= 0, "TaskGroup missing"
    assert barrier_at < taskgroup_at, (
        "drain barrier must run before peer/remote TaskGroup"
    )
    assert "barriers_done=True" in body_src
    assert "fleet stays up until drain completes" in body_src


@pytest.mark.offline
def test_stop_local_services_excludes_giw_from_peer_parallel_ops() -> None:
    """GIW must not be in the parallel peer stop_ops list."""
    source = _FLEET_LOCAL_PY.read_text(encoding="utf-8")
    tree = ast.parse(source)
    fn = None
    for node in tree.body:
        if (
            isinstance(node, ast.AsyncFunctionDef)
            and node.name == "stop_local_services"
        ):
            fn = node
            break
    assert fn is not None
    body_src = ast.get_source_segment(source, fn) or ""
    # Peer stop_ops must not append GIW; barrier owns that stop.
    assert 'stop_ops.append(("git_integration_worker"' not in body_src
    assert "drain_local_barriers_before_stop" in body_src or "barriers_done" in body_src
    assert "GIW is drain-gated" in body_src


@pytest.mark.offline
def test_drain_module_documents_peers_stay_up_invariant() -> None:
    """Drain module docstring carries the operator invariant."""
    source = _FLEET_DRAIN_PY.read_text(encoding="utf-8")
    assert "peers stay up" in source.lower() or "remain up" in source.lower()
    assert "drain_local_barriers_before_stop" in source
    assert "ServiceStatus.UNHEALTHY" in source
    assert "git_worker_kill_for" in source


@pytest.mark.offline
def test_unhealthy_giw_kills_without_supervised_drain() -> None:
    """Wedged GIW (health probe failed) must SIGTERM, not start begin-drain."""

    kill = AsyncMock(return_value="git-integration-worker stopped (PID 1, 0.1s).")
    ctl = SimpleNamespace(
        service_state=SimpleNamespace(
            check_git_integration_worker=lambda: SimpleNamespace(
                status=ServiceStatus.UNHEALTHY
            )
        ),
        git_worker_kill_for=lambda action: kill,
        restart_gate=object(),
        restart_intent_store=object(),
        build_git_worker_drain_supervisor=lambda **_kw: (_ for _ in ()).throw(
            AssertionError("supervised drain must not start")
        ),
    )
    msg = asyncio.run(drain_stop_git_worker(ctl))
    assert "stopped" in msg.lower()
    kill.assert_awaited_once()
