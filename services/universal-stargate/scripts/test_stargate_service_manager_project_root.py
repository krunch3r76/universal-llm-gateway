"""Stargate service manager env: PROJECT_ROOT for lane/fs resolution."""

from __future__ import annotations

import atexit
import os
import sys
from pathlib import Path
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


def _fresh_manager() -> ssm.StargateServiceManager:
    return ssm.StargateServiceManager("default")


def test_setup_environment_sets_project_root_when_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Broken state: Stargate child without PROJECT_ROOT (no .env.local)."""
    monkeypatch.delenv("PROJECT_ROOT", raising=False)
    manager = _fresh_manager()
    manager._setup_environment()
    assert os.environ["PROJECT_ROOT"] == ssm._PROJECT_ROOT


def test_setup_environment_preserves_explicit_project_root(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    explicit = str(tmp_path / "custom-projects-root")
    monkeypatch.setenv("PROJECT_ROOT", explicit)
    manager = _fresh_manager()
    manager._setup_environment()
    assert os.environ["PROJECT_ROOT"] == explicit


def test_cleanup_with_no_spawned_process_does_not_terminate_port_holder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exit cleanup must not signal whatever holds STARGATE_PORT (e.g. live proxy)."""
    manager = _fresh_manager()
    terminate = MagicMock()
    monkeypatch.setattr(manager.process_manager, "terminate_process_tree", terminate)

    # Port 9999 may be held by production Stargate during CI/local runs.
    manager._cleanup()

    terminate.assert_not_called()
