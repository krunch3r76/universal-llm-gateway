"""Stargate service manager env: PROJECT_ROOT for lane/fs resolution."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import stargate_service_manager as ssm  # noqa: E402


def test_setup_environment_sets_project_root_when_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Broken state: Stargate child without PROJECT_ROOT (no .env.local)."""
    monkeypatch.delenv("PROJECT_ROOT", raising=False)
    manager = ssm.StargateServiceManager("default")
    manager._setup_environment()
    assert os.environ["PROJECT_ROOT"] == ssm._PROJECT_ROOT


def test_setup_environment_preserves_explicit_project_root(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    explicit = str(tmp_path / "custom-projects-root")
    monkeypatch.setenv("PROJECT_ROOT", explicit)
    manager = ssm.StargateServiceManager("default")
    manager._setup_environment()
    assert os.environ["PROJECT_ROOT"] == explicit
