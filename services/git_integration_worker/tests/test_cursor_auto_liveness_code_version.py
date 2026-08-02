"""Liveness snapshot exposes code_version and pid for proof-of-live probes."""

from __future__ import annotations

import os
from unittest.mock import patch

from services.git_integration_worker.cursor_auto.liveness import AutoLivenessRegistry


def test_liveness_snapshot_includes_code_version_and_pid():
    reg = AutoLivenessRegistry()
    reg.register("h1")
    with patch(
        "services.git_integration_worker.cursor_auto.liveness.resolve_code_version",
        return_value="abc123deadbeef",
    ):
        snap = reg.snapshot()
    assert snap["code_version"] == "abc123deadbeef"
    assert snap["pid"] == os.getpid()
    assert "wire_skew_aggregate" in snap
