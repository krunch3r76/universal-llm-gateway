"""Offline tests for systemd transient scope wrapping in host_spawn."""

from __future__ import annotations

from pathlib import Path
from unittest import mock

import pytest

from scripts.model_manager.ui.controller.service_ctl import host_spawn

_ORIGINAL_ARGS = ["/usr/bin/python3", "-m", "uvicorn", "services.rag.main:app"]
_CWD = "/tmp/work"
_ENV = {"PYTHONPATH": "/tmp/libs"}
_LOG = Path("/tmp/logs/test/service.log")


@pytest.fixture(autouse=True)
def _reset_systemd_availability_cache() -> None:
    host_spawn._systemd_scope_wrapping_available = None
    host_spawn._systemd_unavailable_reason_cached = None
    yield
    host_spawn._systemd_scope_wrapping_available = None
    host_spawn._systemd_unavailable_reason_cached = None


@pytest.fixture
def popen_mock() -> mock.Mock:
    proc = mock.Mock()
    proc.pid = 4242
    with mock.patch.object(
        host_spawn.subprocess, "Popen", return_value=proc
    ) as patched:
        yield patched


@pytest.fixture
def log_open_mock() -> mock.Mock:
    fh = mock.Mock()
    with mock.patch.object(Path, "open", return_value=fh) as patched:
        yield patched


@pytest.mark.offline
def test_wrapped_argv_when_scope_name_and_systemd_available(
    popen_mock: mock.Mock,
    log_open_mock: mock.Mock,
) -> None:
    with mock.patch.object(
        host_spawn, "is_systemd_scope_wrapping_available", return_value=True
    ):
        host_spawn.spawn_detached_host_process(
            _ORIGINAL_ARGS,
            cwd=_CWD,
            env=_ENV,
            log_file=_LOG,
            scope_name="rag",
        )

    argv = popen_mock.call_args.args[0]
    assert argv[:6] == [
        "systemd-run",
        "--user",
        "--scope",
        "--collect",
        "--unit=ulg-rag",
        "--",
    ]
    assert argv[6:] == _ORIGINAL_ARGS


@pytest.mark.offline
def test_fallback_to_original_argv_when_systemd_unavailable(
    popen_mock: mock.Mock,
    log_open_mock: mock.Mock,
) -> None:
    with (
        mock.patch.object(
            host_spawn, "is_systemd_scope_wrapping_available", return_value=False
        ),
        mock.patch.object(
            host_spawn,
            "_systemd_unavailable_reason",
            return_value="systemd-run not found in PATH",
        ),
    ):
        host_spawn.spawn_detached_host_process(
            _ORIGINAL_ARGS,
            cwd=_CWD,
            env=_ENV,
            log_file=_LOG,
            scope_name="rag",
        )

    argv = popen_mock.call_args.args[0]
    assert argv == _ORIGINAL_ARGS


@pytest.mark.offline
def test_no_scope_name_leaves_argv_unchanged(
    popen_mock: mock.Mock,
    log_open_mock: mock.Mock,
) -> None:
    host_spawn.spawn_detached_host_process(
        _ORIGINAL_ARGS,
        cwd=_CWD,
        env=_ENV,
        log_file=_LOG,
    )

    argv = popen_mock.call_args.args[0]
    assert argv == _ORIGINAL_ARGS


@pytest.mark.offline
def test_memory_max_only_when_explicitly_passed(
    popen_mock: mock.Mock,
    log_open_mock: mock.Mock,
) -> None:
    with mock.patch.object(
        host_spawn, "is_systemd_scope_wrapping_available", return_value=True
    ):
        host_spawn.spawn_detached_host_process(
            _ORIGINAL_ARGS,
            cwd=_CWD,
            env=_ENV,
            log_file=_LOG,
            scope_name="rag",
            memory_max="200M",
        )

    argv = popen_mock.call_args.args[0]
    assert "-p" in argv
    assert "MemoryMax=200M" in argv

    popen_mock.reset_mock()
    with mock.patch.object(
        host_spawn, "is_systemd_scope_wrapping_available", return_value=True
    ):
        host_spawn.spawn_detached_host_process(
            _ORIGINAL_ARGS,
            cwd=_CWD,
            env=_ENV,
            log_file=_LOG,
            scope_name="rag",
        )

    argv_no_limit = popen_mock.call_args.args[0]
    assert "MemoryMax" not in argv_no_limit


@pytest.mark.offline
def test_popen_still_detached_with_wrapped_argv(
    popen_mock: mock.Mock,
    log_open_mock: mock.Mock,
) -> None:
    with mock.patch.object(
        host_spawn, "is_systemd_scope_wrapping_available", return_value=True
    ):
        host_spawn.spawn_detached_host_process(
            _ORIGINAL_ARGS,
            cwd=_CWD,
            env=_ENV,
            log_file=_LOG,
            scope_name="cdp-ask",
        )

    kwargs = popen_mock.call_args.kwargs
    assert kwargs["stdin"] is host_spawn.subprocess.DEVNULL
    assert kwargs["start_new_session"] is True


@pytest.mark.offline
def test_sanitise_scope_name_maps_disallowed_characters() -> None:
    assert host_spawn.sanitise_scope_name("cdp-ask") == "cdp-ask"
    assert host_spawn.sanitise_scope_name("Event service") == "Event-service"
    assert host_spawn.build_scope_wrapped_argv(_ORIGINAL_ARGS, scope_name="cdp-ask")[
        4
    ] == ("--unit=ulg-cdp-ask")
