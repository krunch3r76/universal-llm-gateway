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


@pytest.fixture(autouse=True)
def _no_stale_scope():
    """Default every test to "no leftover unit owns this name".

    The probes shell out to systemctl, and ``subprocess.run`` drives ``Popen``
    as a context manager — which the Popen mock cannot satisfy. Stubbing them
    keeps these argv-construction tests offline; the stale-name branch has its
    own tests below.
    """
    with (
        mock.patch.object(host_spawn, "scope_unit_is_active", return_value=False),
        mock.patch.object(host_spawn, "clear_stale_scope_unit") as clear,
    ):
        yield clear


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


@pytest.mark.offline
def test_stale_scope_unit_is_released_before_wrapping(
    popen_mock: mock.Mock,
    log_open_mock: mock.Mock,
    _no_stale_scope: mock.Mock,
) -> None:
    """A leftover unit must be reset, or it keeps owning the name.

    ``--collect`` only reaps a scope once its own processes exit, so an
    abnormal exit can leave a failed unit holding the name. systemd-run then
    exits non-zero and the service reads as failed to start.
    """
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

    _no_stale_scope.assert_called_once_with("ulg-rag.scope")
    assert popen_mock.call_args.args[0][0] == "systemd-run"


@pytest.mark.offline
def test_live_scope_falls_back_rather_than_failing_the_spawn(
    popen_mock: mock.Mock,
    log_open_mock: mock.Mock,
) -> None:
    """A still-live predecessor costs isolation for this start, never the start.

    Naming an active unit makes systemd-run exit non-zero. Spawning unwrapped
    is exactly the pre-scope behaviour, so the service still comes up.
    """
    with (
        mock.patch.object(
            host_spawn, "is_systemd_scope_wrapping_available", return_value=True
        ),
        mock.patch.object(host_spawn, "scope_unit_is_active", return_value=True),
    ):
        host_spawn.spawn_detached_host_process(
            _ORIGINAL_ARGS,
            cwd=_CWD,
            env=_ENV,
            log_file=_LOG,
            scope_name="rag",
        )

    assert popen_mock.call_args.args[0] == _ORIGINAL_ARGS
    assert popen_mock.call_args.kwargs["start_new_session"] is True


@pytest.mark.offline
def test_scope_unit_name_is_sanitised_and_suffixed() -> None:
    assert host_spawn.scope_unit_name("cdp-ask") == "ulg-cdp-ask.scope"
    assert host_spawn.scope_unit_name("git integration worker") == (
        "ulg-git-integration-worker.scope"
    )
