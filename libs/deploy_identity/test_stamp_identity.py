"""Hermetic tests for stamp_identity deploy-gate field extraction."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from deploy_identity.source_sync_stamp import format_source_sync_stamp
from deploy_identity.stamp_identity import (
    deploy_identity_from_stamp_text,
    read_mcp_container_deploy_identity,
)

pytestmark = pytest.mark.offline

_SHA = "a28906fc180db15916f61f00eb61a633b7781689"
_TREE = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
_SYNCED = "2026-06-02T21:30:00Z"
_STARTED_BEFORE = "2026-06-02T21:00:00.000000000Z"
_STARTED_AFTER = "2026-06-02T22:00:00.000000000Z"


def test_deploy_identity_from_stamp_text_full() -> None:
    text = (
        format_source_sync_stamp(_SHA, synced_at=_SYNCED)
        + f"source_tree_sha={_TREE}\n"
    )
    got = deploy_identity_from_stamp_text(text)
    assert got == {
        "deploy_mode": "source_synced",
        "source_synced_at": _SYNCED,
    }
    assert "source_ref" not in got
    assert "source_tree_hash" not in got


def test_deploy_identity_from_stamp_text_empty() -> None:
    assert deploy_identity_from_stamp_text("") == {}


def test_read_mcp_container_unreadable() -> None:
    with patch(
        "deploy_identity.stamp_identity.subprocess.run",
        return_value=type("P", (), {"returncode": 1, "stdout": b"", "stderr": b"err"})(),
    ):
        fields, reason = read_mcp_container_deploy_identity()
    assert fields == {}
    assert reason is not None and reason.startswith("stamp_exec:")


def test_read_mcp_container_ok(tmp_path: Path) -> None:
    stamp = tmp_path / "stamp"
    stamp.write_text(
        format_source_sync_stamp(_SHA, synced_at="2026-08-01T12:00:00Z"),
        encoding="utf-8",
    )
    stamp_bytes = stamp.read_bytes()

    def _run(cmd: list[str], **_kwargs: object) -> object:
        if "cat" in cmd:
            return type(
                "P",
                (),
                {"returncode": 0, "stdout": stamp_bytes, "stderr": b""},
            )()
        if cmd[:3] == ["docker", "inspect", "-f"]:
            return type(
                "P",
                (),
                {
                    "returncode": 0,
                    "stdout": b"2026-08-01T13:00:00.000000000Z",
                    "stderr": b"",
                },
            )()
        raise AssertionError(f"unexpected cmd: {cmd}")

    with patch("deploy_identity.stamp_identity.subprocess.run", side_effect=_run):
        fields, reason = read_mcp_container_deploy_identity()
    assert reason is None
    assert fields["deploy_mode"] == "source_synced"
    assert "source_ref" not in fields


def test_read_mcp_stamp_newer_than_process() -> None:
    text = format_source_sync_stamp(_SHA, synced_at=_SYNCED)

    def _run(cmd: list[str], **_kwargs: object) -> object:
        if "cat" in cmd:
            return type(
                "P",
                (),
                {"returncode": 0, "stdout": text.encode(), "stderr": b""},
            )()
        return type(
            "P",
            (),
            {"returncode": 0, "stdout": _STARTED_BEFORE.encode(), "stderr": b""},
        )()

    with patch("deploy_identity.stamp_identity.subprocess.run", side_effect=_run):
        fields, reason = read_mcp_container_deploy_identity()
    assert fields == {}
    assert reason == "stamp_newer_than_process"


def test_read_mcp_started_after_sync_succeeds() -> None:
    text = format_source_sync_stamp(_SHA, synced_at=_SYNCED)

    def _run(cmd: list[str], **_kwargs: object) -> object:
        if "cat" in cmd:
            return type(
                "P",
                (),
                {"returncode": 0, "stdout": text.encode(), "stderr": b""},
            )()
        return type(
            "P",
            (),
            {"returncode": 0, "stdout": _STARTED_AFTER.encode(), "stderr": b""},
        )()

    with patch("deploy_identity.stamp_identity.subprocess.run", side_effect=_run):
        fields, reason = read_mcp_container_deploy_identity()
    assert reason is None
    assert fields["source_synced_at"] == _SYNCED


def test_read_mcp_unparseable_started_at() -> None:
    text = format_source_sync_stamp(_SHA, synced_at=_SYNCED)

    def _run(cmd: list[str], **_kwargs: object) -> object:
        if "cat" in cmd:
            return type(
                "P",
                (),
                {"returncode": 0, "stdout": text.encode(), "stderr": b""},
            )()
        return type(
            "P",
            (),
            {"returncode": 0, "stdout": b"not-a-timestamp", "stderr": b""},
        )()

    with patch("deploy_identity.stamp_identity.subprocess.run", side_effect=_run):
        fields, reason = read_mcp_container_deploy_identity()
    assert fields == {}
    assert reason == "stamp_started_at_unknown"


def test_read_mcp_docker_exec_timeout() -> None:
    import subprocess

    with patch(
        "deploy_identity.stamp_identity.subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd="docker", timeout=4.0),
    ):
        fields, reason = read_mcp_container_deploy_identity()
    assert fields == {}
    assert reason == "stamp_exec:TimeoutExpired"


def test_read_mcp_shared_budget_omits_inspect_when_budget_exhausted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Second docker call must not exceed the manage-health deadline budget."""
    text = format_source_sync_stamp(_SHA, synced_at=_SYNCED)
    calls: list[list[str]] = []
    times = iter([1000.0, 1000.0, 1002.0])

    def _monotonic() -> float:
        return next(times, 1002.0)

    monkeypatch.setattr("deploy_identity.stamp_identity.time.monotonic", _monotonic)

    def _run(cmd: list[str], **_kwargs: object) -> object:
        calls.append(cmd)
        if "cat" in cmd:
            return type(
                "P",
                (),
                {"returncode": 0, "stdout": text.encode(), "stderr": b""},
            )()
        raise AssertionError(f"unexpected cmd: {cmd}")

    with patch("deploy_identity.stamp_identity.subprocess.run", side_effect=_run):
        fields, reason = read_mcp_container_deploy_identity(budget_s=1.0)
    assert fields == {}
    assert reason == "stamp_budget_exhausted"
    assert len(calls) == 1
