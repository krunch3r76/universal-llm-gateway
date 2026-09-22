"""Bridge death caller envelope — cwd forensics and classified message/code."""

from __future__ import annotations

from cursor_sdk.errors import NetworkError

from services.git_integration_worker.cursor_sdk_closeout.bridge_caller_error import (
    bridge_failure_delivery_from_forensics,
)
from services.git_integration_worker.routes.cursor_sdk import SdkRunAbortedError


def test_spawn_enoent_death_surfaces_classified_code_not_bare_network() -> None:
    forensics = {
        "bridge_death_class": "spawn_enoent_missing_cwd",
        "bridge_spawn_cwd": "/tmp/missing-lane",
        "bridge_spawn_cwd_exists": False,
        "bridge_stderr_tail": ["Error: spawn /bin/bash ENOENT"],
        "cause": "ConnectError: [Errno 111] Connection refused",
    }
    wrapped = SdkRunAbortedError("Bridge request failed: connection refused", forensics=forensics)
    wrapped.__cause__ = NetworkError("Bridge request failed: ConnectError: [Errno 111] Connection refused")

    delivery = bridge_failure_delivery_from_forensics(forensics=forensics, exc=wrapped)
    assert delivery is not None
    assert delivery.code == "CURSOR_SDK_BRIDGE_SPAWN_CWD"
    assert delivery.retryable is False
    assert "spawn_enoent_missing_cwd" in delivery.message
    assert "spawn_cwd=/tmp/missing-lane" in delivery.message
    assert delivery.message.startswith("cursor-sdk bridge death (")
    assert "Bridge request failed" not in delivery.message.split("transport=")[0]


def test_unclassified_forensics_do_not_override_envelope() -> None:
    forensics = {
        "bridge_death_class": "unclassified",
        "cause": "ConnectError: [Errno 111] Connection refused",
    }
    assert bridge_failure_delivery_from_forensics(forensics=forensics) is None
