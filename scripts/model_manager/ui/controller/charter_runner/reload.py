"""In-process reload of charter-runner modules + tick loop restart.

Avoids a full ``./manage`` TUI quit when only charter-runner code changed.
Caller (ManageApp.reload_charter_tick) stops the live loop, invokes
``reload_charter_runner_modules``, constructs a fresh ``CharterRunnerTickLoop``
from the reloaded package, then emits ``manage.charter.tick.reloaded`` with the
returned module list.
"""

from __future__ import annotations

import importlib
import sys
from typing import Any

# Leaf → dependent order so reload sees fresh imports.
_MODULE_NAMES: tuple[str, ...] = (
    "scripts.model_manager.ui.controller.charter_runner.executor_defaults",
    "scripts.model_manager.ui.controller.charter_runner.caps",
    "scripts.model_manager.ui.controller.charter_runner.checkpoint_parse",
    "scripts.model_manager.ui.controller.charter_runner.eligibility",
    "scripts.model_manager.ui.controller.charter_runner.window_log",
    "scripts.model_manager.ui.controller.charter_runner.bus_client",
    "scripts.model_manager.ui.controller.charter_runner.dispatch_client",
    "scripts.model_manager.ui.controller.charter_runner.materializer",
    "scripts.model_manager.ui.controller.charter_runner.harvest",
    "scripts.model_manager.ui.controller.charter_runner.tick_loop",
    "scripts.model_manager.ui.controller.charter_runner",
)


def reload_charter_runner_modules() -> list[str]:
    """Reload charter-runner package modules in dependency order.

    Returns the list of module names successfully reloaded.
    """
    reloaded: list[str] = []
    for name in _MODULE_NAMES:
        mod = sys.modules.get(name)
        if mod is None:
            # Import so a later reload of dependents can see it.
            mod = importlib.import_module(name)
        importlib.reload(mod)
        reloaded.append(name)
    return reloaded


def charter_runner_loop_class() -> Any:
    """Return ``CharterRunnerTickLoop`` after modules have been reloaded."""
    pkg = sys.modules.get("scripts.model_manager.ui.controller.charter_runner")
    if pkg is None:
        pkg = importlib.import_module(
            "scripts.model_manager.ui.controller.charter_runner"
        )
    return pkg.CharterRunnerTickLoop
