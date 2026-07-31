"""Quit path must not tear down the fleet (todo:manage-quit-must-not-stop-fleet).

Live repro 2026-07-28: ``q`` exited manage and asyncio ``BaseSubprocessTransport.close``
killed every host service spawned via ``create_subprocess_exec`` (even with
``start_new_session=True``). Docker gateway survived; cortex HTTP forwarder
(Popen) survived. Fix: long-lived host spawns use ``host_spawn.spawn_detached_host_process``.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_UI_ROOT = Path(__file__).resolve().parents[2]
_APP_PY = _UI_ROOT / "app.py"
_UVICORN_PY = _UI_ROOT / "controller" / "service_ctl" / "uvicorn_service.py"
_EVENT_PY = _UI_ROOT / "controller" / "service_ctl" / "event_service.py"
_HOST_SPAWN_PY = _UI_ROOT / "controller" / "service_ctl" / "host_spawn.py"
_FLEET_STOP_NAMES = frozenset(
    {
        "stop_local_services",
        "FleetOrchestrator",
        "sync_restart_all",
        "fleet_sync_restart",
    }
)


def _method_source(tree: ast.AST, class_name: str, name: str, path: Path) -> str:
    for node in tree.body:  # type: ignore[attr-defined]
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if item.name == name:
                        return (
                            ast.get_source_segment(path.read_text(encoding="utf-8"), item)
                            or ""
                        )
    raise AssertionError(f"{class_name}.{name} not found in {path}")


@pytest.mark.offline
def test_quit_handlers_do_not_call_fleet_stop() -> None:
    """action_quit / on_unmount / _drain_then_exit must not invoke fleet stop."""
    source = _APP_PY.read_text(encoding="utf-8")
    tree = ast.parse(source)
    for method in ("action_quit", "on_unmount", "_drain_then_exit"):
        body = _method_source(tree, "ModelManagerApp", method, _APP_PY)
        for banned in _FLEET_STOP_NAMES:
            assert banned not in body, (
                f"ModelManagerApp.{method} must not reference {banned}"
            )


@pytest.mark.offline
def test_app_module_does_not_import_fleet_stop() -> None:
    """Top-level app imports must not pull fleet stop into the quit module."""
    source = _APP_PY.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if "fleet" in mod:
                for alias in node.names:
                    imported.add(alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if "fleet" in alias.name:
                    imported.add(alias.name)
    assert not (_FLEET_STOP_NAMES & imported)


@pytest.mark.offline
def test_long_lived_host_spawns_use_popen_not_asyncio_subprocess() -> None:
    """uvicorn + event_service start paths must not create asyncio transports."""
    for path in (_UVICORN_PY, _EVENT_PY):
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and (
                (
                    isinstance(node.func, ast.Attribute)
                    and node.func.attr == "create_subprocess_exec"
                )
                or (
                    isinstance(node.func, ast.Name)
                    and node.func.id == "create_subprocess_exec"
                )
            )
        ]
        assert not calls, (
            f"{path.name} must not call create_subprocess_exec for host services "
            f"(asyncio transport kill-on-loop-close); found {len(calls)}"
        )
        assert "spawn_detached_host_process" in source
    host_spawn = _HOST_SPAWN_PY.read_text(encoding="utf-8")
    assert "subprocess.Popen" in host_spawn
    assert "start_new_session=True" in host_spawn
