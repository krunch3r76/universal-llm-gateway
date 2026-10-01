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

    def _calls_in(node: ast.AST) -> set[str]:
        names: set[str] = set()
        for sub in ast.walk(node):
            if isinstance(sub, ast.Call):
                if isinstance(sub.func, ast.Name) and sub.func.id in loop_names:
                    names.add(sub.func.id)
                if isinstance(sub.func, ast.Attribute) and sub.func.attr in loop_names:
                    names.add(sub.func.attr)
        return names

    assert loop_names <= _calls_in(start_fn)
    on_mount_loops = _calls_in(on_mount) - {"StargateHealthRestart"}
    assert on_mount_loops == set(), f"on_mount must not start loops: {on_mount_loops}"

    for fn_name in ("_retry_api_server", "_park_for_handover"):
        fn_node = methods[fn_name]
        bound_calls = {
            n.func.attr
            for n in ast.walk(fn_node)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
        }
        assert "_start_bound_loops" in bound_calls


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
