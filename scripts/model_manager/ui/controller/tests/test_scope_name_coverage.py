"""Every host-service spawn must name a cgroup scope.

The a:33656 isolation pass made ``scope_name`` a required argument so that each
long-lived service lands in its own cgroup instead of the shared tmux pane
scope. Two things then went wrong, both invisible to the suite:

- ``email_bridge_service`` was never updated, so ``start_email_bridge`` raised
  ``TypeError: missing a required argument: 'scope_name'``. It stayed hidden
  because the running process predated the change — nothing restarts
  email-bridge in a normal session, and no test starts it. The pager rides on
  email-bridge, so the failure would have surfaced as a silent loss of alerting.
- the cortex-api HTTP forwarder used a bare ``subprocess.Popen`` and so was
  never subject to the requirement at all.

A signature requirement only protects call sites a test actually executes.
These are static checks over the source, so an unexercised call site is still
covered.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_SERVICE_CTL = Path(__file__).resolve().parent.parent / "service_ctl"

# Spawn helpers that place a process in a cgroup scope. Any call to one of
# these for a long-lived service must say which scope.
_SPAWN_HELPERS = {"_start_uvicorn_service", "spawn_detached_host_process"}


def _call_name(node: ast.Call) -> str | None:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


def _spawn_calls() -> list[tuple[Path, ast.Call]]:
    found: list[tuple[Path, ast.Call]] = []
    for path in _SERVICE_CTL.rglob("*.py"):
        if path.name.startswith("test_"):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and _call_name(node) in _SPAWN_HELPERS:
                found.append((path, node))
    return found


@pytest.mark.offline
def test_every_spawn_call_names_a_scope() -> None:
    missing = [
        f"{path.name}:{node.lineno} calls {_call_name(node)}() without scope_name"
        for path, node in _spawn_calls()
        # The helper's own definition site forwards the caller's value.
        if path.name not in {"uvicorn_service.py", "host_spawn.py"}
        and not any(kw.arg == "scope_name" for kw in node.keywords)
    ]
    assert not missing, "unscoped host-service spawn(s):\n  " + "\n  ".join(missing)


@pytest.mark.offline
def test_the_guard_actually_sees_call_sites() -> None:
    """Guard against the guard silently matching nothing.

    Without this, a rename of the spawn helpers would empty the search and the
    check above would pass vacuously — the same 'green because it tested
    nothing' failure that let the inert scope probe ship.
    """
    calls = _spawn_calls()
    assert len(calls) >= 5, f"expected several spawn call sites, found {len(calls)}"


@pytest.mark.offline
def test_scope_names_are_unique_per_service() -> None:
    """Two services sharing a scope name would share a kill unit again."""
    names: dict[str, str] = {}
    collisions: list[str] = []
    for path, node in _spawn_calls():
        for kw in node.keywords:
            if kw.arg == "scope_name" and isinstance(kw.value, ast.Constant):
                value = str(kw.value.value)
                if value in names and names[value] != path.name:
                    collisions.append(f"{value!r}: {names[value]} and {path.name}")
                names[value] = path.name
    assert not collisions, "scope name collisions:\n  " + "\n  ".join(collisions)
