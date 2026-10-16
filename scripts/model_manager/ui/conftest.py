"""Hermetic guard for the controller tree and charter-harvest tests only."""

from __future__ import annotations

import os

# Before any test import starts the process-lifetime events publisher.
# A later fixture undo must not be followed by a real connect (a:39456).
os.environ["CORTEX_EVENTS_ENABLED"] = "false"

import pytest

from scripts.model_manager.testing.hermetic_network_guard import (
    ALLOW_LIVE_NETWORK_MARKER,
    install_hermetic_network_guard,
    path_is_guarded,
)
from scripts.model_manager.ui.controller.tests.conftest import stub_process_live_probe


@pytest.fixture(autouse=True)
def _hermetic_network_guard_for_named_trees(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Install the guard only under controller/ and the charter-harvest module.

    A plugin-level autouse fixture would cover every test in the session,
    including ``ui/tests/`` listeners and files outside this tree.
    """
    node_path = str(getattr(request.node, "path", ""))
    if not path_is_guarded(node_path):
        return
    if request.node.get_closest_marker(ALLOW_LIVE_NETWORK_MARKER):
        return
    install_hermetic_network_guard(monkeypatch)


@pytest.fixture(autouse=True)
def _ui_stub_process_live_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    stub_process_live_probe(monkeypatch)
