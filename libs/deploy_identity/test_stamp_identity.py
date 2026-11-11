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
_TREE = "b" * 40


def test_deploy_identity_from_stamp_text_full() -> None:
    text = (
        format_source_sync_stamp(_SHA, synced_at="2026-06-02T21:30:00Z")
        + f"source_tree_sha={_TREE}\n"
    )
    got = deploy_identity_from_stamp_text(text)
    assert got == {
        "deploy_mode": "source_synced",
        "source_synced_at": "2026-06-02T21:30:00Z",
        "source_ref": f"git:{_SHA}",
        "source_tree_hash": _TREE,
    }


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

    class _Proc:
        returncode = 0
        stdout = stamp.read_bytes()
        stderr = b""

    with patch(
        "deploy_identity.stamp_identity.subprocess.run",
        return_value=_Proc(),
    ):
        fields, reason = read_mcp_container_deploy_identity()
    assert reason is None
    assert fields["source_ref"] == f"git:{_SHA}"
    assert fields["deploy_mode"] == "source_synced"
