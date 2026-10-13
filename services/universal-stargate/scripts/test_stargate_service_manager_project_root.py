"""Stargate service manager env: PROJECT_ROOT for lane/fs resolution."""

from __future__ import annotations

import atexit
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import stargate_service_manager as ssm  # noqa: E402


@pytest.fixture(autouse=True)
def _suppress_manager_atexit(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test processes must not register manager exit hooks that run on pytest teardown."""
    monkeypatch.setattr(atexit, "register", lambda *_args, **_kwargs: None)


@dataclass
class _PatchedManager:
    manager: ssm.StargateServiceManager
    terminate_process_tree: MagicMock
    find_stargate_processes: MagicMock
    find_processes_on_port: MagicMock


def _fake_port_holder() -> MagicMock:
    holder = MagicMock(name="port_holder_process")
    holder.pid = 424242
    return holder


def _patched_manager(
    monkeypatch: pytest.MonkeyPatch,
    *,
    find_stargate_return: list[Any] | None = None,
    find_port_return: list[Any] | None = None,
) -> _PatchedManager:
    """Construct StargateServiceManager with lifecycle/process discovery fully stubbed."""
    holder = _fake_port_holder()
    find_stargate = MagicMock(
        return_value=find_stargate_return if find_stargate_return is not None else [holder]
    )
    find_port = MagicMock(
        return_value=find_port_return if find_port_return is not None else [holder]
    )
    terminate = MagicMock()

    monkeypatch.setattr(
        ssm.ProcessManager,
        "find_stargate_processes",
        find_stargate,
    )
    monkeypatch.setattr(
        ssm.ProcessManager,
        "find_processes_on_port",
        find_port,
    )
    monkeypatch.setattr(
        ssm.ProcessManager,
        "terminate_process_tree",
        terminate,
    )

    manager = ssm.StargateServiceManager("default")
    return _PatchedManager(
        manager=manager,
        terminate_process_tree=terminate,
        find_stargate_processes=find_stargate,
        find_processes_on_port=find_port,
    )


def test_setup_environment_sets_project_root_when_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Broken state: Stargate child without PROJECT_ROOT (no .env.local)."""
    monkeypatch.delenv("PROJECT_ROOT", raising=False)
    patched = _patched_manager(monkeypatch)
    patched.manager._setup_environment()
    assert os.environ["PROJECT_ROOT"] == ssm._PROJECT_ROOT


def test_setup_environment_preserves_explicit_project_root(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    explicit = str(tmp_path / "custom-projects-root")
    monkeypatch.setenv("PROJECT_ROOT", explicit)
    patched = _patched_manager(monkeypatch)
    patched.manager._setup_environment()
    assert os.environ["PROJECT_ROOT"] == explicit


def test_cleanup_with_no_spawned_process_does_not_terminate_port_holder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exit cleanup must not signal port holders when this manager spawned nothing."""
    patched = _patched_manager(monkeypatch)
    assert patched.manager.stargate_process is None

    patched.manager._cleanup()

    patched.terminate_process_tree.assert_not_called()
    patched.find_stargate_processes.assert_not_called()
    patched.find_processes_on_port.assert_not_called()


def test_cleanup_terminates_only_spawned_stargate_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When stargate_process is set and running, cleanup terminates exactly that process."""
    patched = _patched_manager(monkeypatch)
    spawned = MagicMock(name="spawned_stargate_process")
    spawned.is_running.return_value = True
    patched.manager.stargate_process = spawned

    patched.manager._cleanup()

    patched.terminate_process_tree.assert_called_once()
    (proc_arg,), kwargs = patched.terminate_process_tree.call_args
    assert proc_arg is spawned
    assert kwargs.get("timeout") == patched.manager.config.shutdown_grace
    patched.find_stargate_processes.assert_not_called()
    patched.find_processes_on_port.assert_not_called()
