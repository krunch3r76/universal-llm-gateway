"""Hermetic tests for MCP restart gate decisions and docker argv plans."""

from __future__ import annotations

from pathlib import Path

import pytest

from deploy_identity.mcp_restart_gate import (
    GateInput,
    gate_allows_restore,
    plan_restore_commands,
    read_booted,
    read_pending,
    snapshot_present,
    source_tree_sha,
    write_pending,
)

pytestmark = pytest.mark.offline


def test_gate_refuses_when_pending_equals_booted(tmp_path: Path) -> None:
    base = tmp_path / "last-healthy"
    base.mkdir()
    (base / "PENDING").write_text("abc\n")
    (base / "BOOTED").write_text("abc\n")
    gate = GateInput(
        mcp_data_dir=tmp_path,
        container_status="unhealthy",
        pending=read_pending(tmp_path),
        booted=read_booted(tmp_path),
        hold=False,
        restart_in_progress=False,
        firer="script",
    )
    assert gate_allows_restore(gate) is False


def test_gate_allows_unhealthy_pending_mismatch(tmp_path: Path) -> None:
    write_pending(tmp_path, "incoming")
    (tmp_path / "last-healthy" / "BOOTED").write_text("old\n")
    gate = GateInput(
        mcp_data_dir=tmp_path,
        container_status="unhealthy",
        pending=read_pending(tmp_path),
        booted=read_booted(tmp_path),
        hold=False,
        restart_in_progress=False,
        firer="script",
    )
    assert gate_allows_restore(gate) is True


def test_gate_refuses_healthy_container(tmp_path: Path) -> None:
    write_pending(tmp_path, "incoming")
    gate = GateInput(
        mcp_data_dir=tmp_path,
        container_status="healthy",
        pending=read_pending(tmp_path),
        booted=None,
        hold=False,
        restart_in_progress=False,
        firer="script",
    )
    assert gate_allows_restore(gate) is False


def test_gate_backup_respects_restart_in_progress(tmp_path: Path) -> None:
    write_pending(tmp_path, "incoming")
    gate = GateInput(
        mcp_data_dir=tmp_path,
        container_status="exited",
        pending=read_pending(tmp_path),
        booted="old",
        hold=False,
        restart_in_progress=True,
        firer="backup",
    )
    assert gate_allows_restore(gate) is False


def test_plan_restore_no_rm_when_container_absent() -> None:
    plan = plan_restore_commands(
        checked_image_id="sha256:abc",
        container_exists=False,
        compose_args=("docker", "compose", "-f", "mcp-server.yml"),
    )
    assert plan.skip_rm is True
    assert not any(step[:3] == ("docker", "rm", "-f") for step in plan.steps)
    assert ("docker", "tag", "sha256:abc", "universal-mcp-server:local") in plan.steps


def test_plan_restore_includes_rm_when_container_present() -> None:
    plan = plan_restore_commands(
        checked_image_id="sha256:abc",
        container_exists=True,
        compose_args=("docker", "compose", "-f", "mcp-server.yml"),
    )
    assert plan.skip_rm is False
    assert plan.steps[0] == ("docker", "rm", "-f", "mcp-server")


def test_snapshot_present_requires_roots(tmp_path: Path) -> None:
    base = tmp_path / "last-healthy"
    base.mkdir()
    assert snapshot_present(tmp_path) is False
    for name in ("libs", "services", "config", "pipelines"):
        (base / name).mkdir()
    (base / "sitecustomize.py").write_text("# stub\n")
    assert snapshot_present(tmp_path) is True


def test_source_tree_sha_stable_empty_libs(tmp_path: Path) -> None:
    (tmp_path / "libs").mkdir()
    (tmp_path / "services" / "mcp-server").mkdir(parents=True)
    h1 = source_tree_sha(tmp_path)
    h2 = source_tree_sha(tmp_path)
    assert h1 == h2
