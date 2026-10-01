"""Hermetic tests for the manage single-instance guard (friction a:27437).

Two mechanisms are covered:

* the exclusive ``flock`` that makes a second launch fail before Textual runs;
* the ``ManageSocketBusyError`` path in ``app.on_mount``, which must exit the
  process instead of continuing socket-less with the charter/digest loops up.

The second is asserted structurally (AST over ``app.py``) so the test stays
hermetic — constructing ``ModelManagerApp`` would boot the catalog, the event
bus, and the service controller.
"""

from __future__ import annotations

import ast
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from scripts.model_manager.ui.__main__ import main
from scripts.model_manager.ui.single_instance import (
    ManageAlreadyRunningError,
    acquire_manage_lock,
    release_manage_lock,
)

_APP_PY = Path(__file__).resolve().parents[1] / "app.py"


@pytest.mark.offline
def test_second_acquire_fails_while_first_holds(tmp_path: Path) -> None:
    lock_path = tmp_path / "manage.lock"
    fd = acquire_manage_lock(lock_path)
    try:
        with pytest.raises(ManageAlreadyRunningError):
            acquire_manage_lock(lock_path)
    finally:
        release_manage_lock(fd)


@pytest.mark.offline
def test_lock_is_reacquirable_after_release(tmp_path: Path) -> None:
    lock_path = tmp_path / "manage.lock"
    release_manage_lock(acquire_manage_lock(lock_path))
    release_manage_lock(acquire_manage_lock(lock_path))


@pytest.mark.offline
def test_lock_excludes_a_separate_process(tmp_path: Path) -> None:
    """flock is a kernel property, not an in-process convention."""
    lock_path = tmp_path / "manage.lock"
    fd = acquire_manage_lock(lock_path)
    probe = textwrap.dedent(
        f"""
        import fcntl, os, sys
        fd = os.open({str(lock_path)!r}, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            sys.exit(7)
        sys.exit(0)
        """
    )
    try:
        result = subprocess.run(  # noqa: S603
            [sys.executable, "-c", probe], capture_output=True, timeout=30, check=False
        )
    finally:
        release_manage_lock(fd)
    assert result.returncode == 7, result.stderr.decode()


@pytest.mark.offline
def test_main_exits_without_running_when_lock_held(
    capsys: pytest.CaptureFixture[str],
) -> None:
    called: list[str] = []

    def _refuse() -> int:
        raise ManageAlreadyRunningError("error: another manage instance holds X\n")

    code = main(
        [],
        stdin_isatty=True,
        run_fn=lambda: called.append("run"),
        acquire_lock_fn=_refuse,
        release_lock_fn=lambda fd: None,
    )

    assert code == 3
    assert called == []
    assert "another manage instance" in capsys.readouterr().err


@pytest.mark.offline
def test_main_releases_lock_after_run() -> None:
    released: list[int] = []

    code = main(
        [],
        stdin_isatty=True,
        run_fn=lambda: None,
        acquire_lock_fn=lambda: 42,
        release_lock_fn=released.append,
    )

    assert code == 0
    assert released == [42]


def _app_class_methods() -> dict[str, ast.AsyncFunctionDef]:
    tree = ast.parse(_APP_PY.read_text())
    methods: dict[str, ast.AsyncFunctionDef] = {}
    for node in tree.body:
        if not isinstance(node, ast.ClassDef) or node.name != "ModelManagerApp":
            continue
        for item in node.body:
            if isinstance(item, ast.AsyncFunctionDef):
                methods[item.name] = item
    if "on_mount" not in methods:
        raise AssertionError("on_mount not found in app.py")
    return methods


def _on_mount_node() -> ast.AsyncFunctionDef:
    return _app_class_methods()["on_mount"]


def _busy_handler(on_mount: ast.AsyncFunctionDef) -> ast.ExceptHandler:
    for node in ast.walk(on_mount):
        if (
            isinstance(node, ast.ExceptHandler)
            and isinstance(node.type, ast.Name)
            and node.type.id == "ManageSocketBusyError"
        ):
            return node
    raise AssertionError("ManageSocketBusyError handler not found in on_mount")


@pytest.mark.offline
def test_busy_socket_handler_exits_and_returns() -> None:
    handler = _busy_handler(_on_mount_node())
    armed_branch = None
    armed_idx = -1
    for idx, node in enumerate(handler.body):
        if isinstance(node, ast.If):
            armed_branch = node
            armed_idx = idx
            break
    assert armed_branch is not None, "armed vs non-armed branch required"

    armed_body = armed_branch.body
    non_armed = handler.body[armed_idx + 1 :]
    non_armed_calls = {
        node.func.attr
        for node in ast.walk(ast.Module(body=non_armed, type_ignores=[]))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert "exit" in non_armed_calls
    armed_timer_targets = {
        node.args[1].attr
        for node in ast.walk(ast.Module(body=armed_body, type_ignores=[]))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "set_timer"
        and len(node.args) >= 2
        and isinstance(node.args[1], ast.Attribute)
    }
    assert "_park_for_handover" in armed_timer_targets
    armed_returns = [
        node
        for node in ast.walk(ast.Module(body=armed_body, type_ignores=[]))
        if isinstance(node, ast.Return)
    ]
    assert armed_returns, "armed busy handler must return after scheduling park"
    assert isinstance(armed_body[-1], ast.Return), (
        "armed ManageSocketBusyError branch must end with return"
    )


def _handler_last_is_return(handler: ast.ExceptHandler) -> bool:
    if not handler.body:
        return False
    return isinstance(handler.body[-1], ast.Return)


def _try_has_server_start(try_node: ast.Try) -> bool:
    for sub in ast.walk(ast.Module(body=try_node.body, type_ignores=[])):
        if (
            isinstance(sub, ast.Call)
            and isinstance(sub.func, ast.Attribute)
            and sub.func.attr == "start"
        ):
            return True
    return False


def _start_bound_loops_lineno_in_try_body(try_node: ast.Try) -> set[int]:
    return {
        sub.lineno
        for sub in ast.walk(ast.Module(body=try_node.body, type_ignores=[]))
        if isinstance(sub, ast.Call)
        and isinstance(sub.func, ast.Attribute)
        and sub.func.attr == "_start_bound_loops"
    }


def _bound_loops_in_same_try_as_server_start(fn_node: ast.AsyncFunctionDef) -> bool:
    """Every _start_bound_loops call must sit in a try that also awaits server.start()."""
    all_linenos = {c.lineno for c in _start_bound_loops_calls(fn_node)}
    if not all_linenos:
        return True
    covered: set[int] = set()
    for try_node in ast.walk(fn_node):
        if not isinstance(try_node, ast.Try):
            continue
        if not _try_has_server_start(try_node):
            continue
        covered |= _start_bound_loops_lineno_in_try_body(try_node)
    return all_linenos <= covered


def _loop_call_names(node: ast.AST) -> set[str]:
    loop_names = {
        "reconcile_pending_restart_intents",
        "DigestTickLoop",
        "CharterRunnerTickLoop",
        "StargateHealthRestart",
    }
    names: set[str] = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call):
            if isinstance(sub.func, ast.Name) and sub.func.id in loop_names:
                names.add(sub.func.id)
            if isinstance(sub.func, ast.Attribute) and sub.func.attr in loop_names:
                names.add(sub.func.attr)
    return names


def _start_bound_loops_calls(node: ast.AST) -> list[ast.Call]:
    return [
        sub
        for sub in ast.walk(node)
        if isinstance(sub, ast.Call)
        and isinstance(sub.func, ast.Attribute)
        and sub.func.attr == "_start_bound_loops"
    ]


def _try_has_start_before_bound_loops(try_node: ast.Try) -> bool:
    start_seen = False
    for stmt in try_node.body:
        for sub in ast.walk(stmt):
            if (
                isinstance(sub, ast.Call)
                and isinstance(sub.func, ast.Attribute)
                and sub.func.attr == "start"
            ):
                start_seen = True
            if (
                isinstance(sub, ast.Call)
                and isinstance(sub.func, ast.Attribute)
                and sub.func.attr == "_start_bound_loops"
            ):
                if not start_seen:
                    return False
    return start_seen


@pytest.mark.offline
def test_bound_loops_only_in_start_bound_loops() -> None:
    methods = _app_class_methods()
    on_mount = methods["on_mount"]
    start_fn = methods["_start_bound_loops"]

    loop_names = {
        "reconcile_pending_restart_intents",
        "DigestTickLoop",
        "CharterRunnerTickLoop",
        "StargateHealthRestart",
    }

    assert loop_names <= _loop_call_names(start_fn)
    assert _loop_call_names(on_mount) == set(), (
        f"on_mount must not start loops: {_loop_call_names(on_mount)}"
    )

    for fn_name in ("on_mount", "_retry_api_server", "_park_for_handover"):
        fn_node = methods[fn_name]
        assert _loop_call_names(fn_node) == set(), (
            f"{fn_name} must not call loop names directly"
        )
        for handler in ast.walk(fn_node):
            if not isinstance(handler, ast.ExceptHandler):
                continue
            if isinstance(handler.type, ast.Name) and handler.type.id == (
                "ManageSocketBusyError"
            ):
                assert _start_bound_loops_calls(handler) == []
                assert any(isinstance(n, ast.Return) for n in handler.body)
                assert _handler_last_is_return(handler), (
                    f"{fn_name} ManageSocketBusyError handler must end with return"
                )

        for try_node in ast.walk(fn_node):
            if not isinstance(try_node, ast.Try):
                continue
            if not _start_bound_loops_calls(try_node):
                continue
            assert _try_has_start_before_bound_loops(try_node), (
                f"{fn_name} must call .start() before _start_bound_loops in try"
            )
            for handler in try_node.handlers:
                assert _start_bound_loops_calls(handler) == []

        assert _start_bound_loops_calls(fn_node), (
            f"{fn_name} must call _start_bound_loops at least once"
        )
        assert _bound_loops_in_same_try_as_server_start(fn_node), (
            f"{fn_name} must call _start_bound_loops only inside the try "
            "that awaits server.start()"
        )


@pytest.mark.offline
def test_main_armed_defers_lock_and_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from pathlib import Path

    monkeypatch.setenv("MANAGE_HANDOVER_RECORD", str(Path("/tmp/armed.json")))
    acquired: list[str] = []
    ran: list[str] = []

    code = main(
        [],
        stdin_isatty=True,
        run_fn=lambda: ran.append("run"),
        acquire_lock_fn=lambda: acquired.append("lock") or 1,
        release_lock_fn=lambda fd: None,
    )
    assert code == 0
    assert acquired == []
    assert ran == ["run"]


@pytest.mark.offline
def test_startup_conflict_exit_code_is_nonzero() -> None:
    tree = ast.parse(_APP_PY.read_text())
    values = [
        node.value.value
        for node in tree.body
        if isinstance(node, ast.Assign)
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id == "STARTUP_CONFLICT_EXIT_CODE"
        and isinstance(node.value, ast.Constant)
    ]
    assert values == [3]
