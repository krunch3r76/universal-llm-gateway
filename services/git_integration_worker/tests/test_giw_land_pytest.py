"""Land-gate pytest runner — timeouts, worktree PYTHONPATH, module-origin proof."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from services.git_integration_worker import giw_land_pytest as land_pytest
from services.git_integration_worker.config import _DIFF_SCOPED_GATE_SCRIPT


def test_worktree_first_environ_prepends_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prior = "/other/root"
    monkeypatch.setenv("PYTHONPATH", prior)
    root = str(tmp_path.resolve())
    env = land_pytest.worktree_first_environ(tmp_path)
    assert env["PYTHONPATH"] == f"{root}{os.pathsep}{prior}"
    assert env["ULG_LAND_GATE_WORKTREE"] == root


def test_pytest_timeout_argv_includes_timeout_flags() -> None:
    argv = land_pytest.pytest_timeout_argv()
    assert any(part.startswith("--timeout=") for part in argv)
    assert "--timeout-method=thread" in argv


def test_module_origin_proof_passes_in_repo() -> None:
    root = Path(__file__).resolve().parents[3]
    proc = land_pytest.run_module_origin_proof(root)
    assert proc.returncode == 0, proc.stderr
    assert "ULG_MODULE_ORIGIN" in proc.stderr


def test_green_gate_script_uses_land_pytest_runner() -> None:
    assert "services.git_integration_worker.giw_land_pytest run" in _DIFF_SCOPED_GATE_SCRIPT
    assert "python -m pytest " not in _DIFF_SCOPED_GATE_SCRIPT


def test_run_land_gate_pytest_retention_node_passes() -> None:
    root = Path(__file__).resolve().parents[3]
    node = (
        "services/git_integration_worker/tests/test_conductor_lane_retention.py::"
        "test_packet_reference_forbids_land_disposition_before_done"
    )
    proc = land_pytest.run_land_gate_pytest(root, [node, "-q", "--tb=line"])
    assert proc is not None
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "ULG_MODULE_ORIGIN" in proc.stderr


def test_suite_digest_gate_uses_land_runner(monkeypatch: pytest.MonkeyPatch) -> None:
    from services.git_integration_worker import giw_suite_digest_gate as gate

    calls: list[tuple] = []

    def _fake(wt: Path, argv: list[str], *, basetemp: str | None = None, python=None):
        calls.append((wt, argv, basetemp))
        return subprocess.CompletedProcess(args=[], returncode=0, stdout="0 passed\n", stderr="")

    monkeypatch.setattr(gate, "run_land_gate_pytest", _fake)
    out = gate.run_frozen_pytest()
    assert out == ("0 passed\n", "")
    assert len(calls) == 1
    assert calls[0][1] == land_pytest.suite_digest_pytest_argv()
