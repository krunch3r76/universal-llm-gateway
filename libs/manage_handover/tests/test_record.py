"""Hermetic tests for manage handover armed-record primitives."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

import pytest

from libs.manage_handover import (
    prove_armed_record,
    read_armed_record,
    record_path_from_env,
    remove_armed_record,
    write_armed_record,
)


@pytest.mark.offline
def test_write_read_round_trip(tmp_path) -> None:
    path = tmp_path / "manage.armed.json"
    write_armed_record(
        path,
        pid=42,
        process_start_time="2026-08-10T01:00:00+00:00",
        code_version="a" * 40,
    )
    data = read_armed_record(path)
    assert data == {
        "pid": 42,
        "process_start_time": "2026-08-10T01:00:00+00:00",
        "code_version": "a" * 40,
    }


@pytest.mark.offline
def test_remove_idempotent(tmp_path) -> None:
    path = tmp_path / "manage.armed.json"
    remove_armed_record(path)
    write_armed_record(path, pid=1)
    remove_armed_record(path)
    assert read_armed_record(path) is None


@pytest.mark.offline
def test_prove_rejects_dead_pid(tmp_path) -> None:
    record = {
        "pid": 999999999,
        "code_version": "deadbeef",
        "process_start_time": "2026-08-11T00:00:00+00:00",
    }
    ok, reason = prove_armed_record(
        record,
        target_ref="deadbeef",
        whoami_before={
            "process_start_time": "2026-08-10T00:00:00+00:00",
        },
    )
    assert ok is False
    assert "pid_not_alive" in reason


@pytest.mark.offline
def test_prove_rejects_wrong_code_version() -> None:
    record = {
        "pid": os.getpid(),
        "code_version": "wrong",
        "process_start_time": "2026-08-11T00:00:00+00:00",
    }
    ok, reason = prove_armed_record(
        record,
        target_ref="deadbeef",
        whoami_before={"process_start_time": "2026-08-10T00:00:00+00:00"},
    )
    assert ok is False
    assert "code_version_mismatch" in reason


@pytest.mark.offline
def test_prove_rejects_non_later_start_time() -> None:
    record = {
        "pid": os.getpid(),
        "code_version": "deadbeef",
        "process_start_time": "2026-08-10T00:00:00+00:00",
    }
    ok, reason = prove_armed_record(
        record,
        target_ref="deadbeef",
        whoami_before={"process_start_time": "2026-08-10T00:00:00+00:00"},
    )
    assert ok is False
    assert "process_start_time_not_later" in reason


@pytest.mark.offline
def test_prove_accepts_valid_record() -> None:
    later = (datetime.now(UTC) + timedelta(seconds=5)).isoformat()
    record = {
        "pid": os.getpid(),
        "code_version": "deadbeef",
        "process_start_time": later,
    }
    ok, reason = prove_armed_record(
        record,
        target_ref="deadbeef",
        whoami_before={"process_start_time": "2026-08-10T00:00:00+00:00"},
    )
    assert ok is True
    assert reason == "armed"


@pytest.mark.offline
def test_record_path_from_env_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MANAGE_HANDOVER_RECORD", raising=False)
    assert record_path_from_env() is None


@pytest.mark.offline
def test_record_path_from_env_set(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    path = tmp_path / "armed.json"
    monkeypatch.setenv("MANAGE_HANDOVER_RECORD", str(path))
    assert record_path_from_env() == path
