"""Pytest configuration for git-integration-worker tests."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from services.git_integration_worker.cursor_sdk_capture_binding import CaptureBinding
from services.git_integration_worker.cursor_sdk_dispatch_context import (
    SdkDispatchContext,
)
from services.git_integration_worker.tests.cursor_bus_hermetic import (
    install_cursor_bus_hermetic,
    skip_cursor_bus_hermetic_for_node,
)

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from services.git_integration_worker.tests.skill_tree_test_bootstrap import (  # noqa: E402
    maybe_materialize_skill_trees_for_pytest,
)

# Before any test imports ``get_skill_catalog()``, mirror hub gitignored skill SOTs
# into git-clean worktrees (land gate / detached digest trees). Hook: conftest import.
maybe_materialize_skill_trees_for_pytest(_REPO_ROOT)


def _ctx(
    hub: Path,
    *,
    dispatch_id: str,
    thread_id: str,
    dispatch_workspace: Path | None = None,
    binding: CaptureBinding | None = None,
    contract: str = "consult",
) -> SdkDispatchContext:
    """Build an SdkDispatchContext for route tests without touching the filesystem.

    Defaults to a hub Lane-A binding whose write tree, receipt tree and mount
    root are all ``hub`` — the shape the large majority of route tests assume.
    Pass ``binding`` explicitly for Lane-B or satellite cases.
    """
    resolved = hub.resolve()
    return SdkDispatchContext(
        dispatch_id=dispatch_id,
        thread_id=thread_id,
        handoff_contract=contract,
        hub=hub,
        dispatch_workspace=dispatch_workspace
        if dispatch_workspace is not None
        else hub,
        capture_binding=binding
        or CaptureBinding(
            lane="A",
            write_tree=resolved,
            receipt_tree=resolved,
            mount_root=resolved,
            repo_roots=(resolved,),
        ),
    )


@pytest.fixture(autouse=True)
def _cursor_card_probed_at_for_admit_tests(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stamp ``probed_at`` on card entries so admit-path tests stay hermetic."""
    from dataclasses import replace

    import cursor_capabilities
    import cursor_capabilities.cursor_capabilities as cap_mod

    stamped: dict[str, cap_mod.ModelCapability] = {}
    for model_id, cap in cap_mod.CURSOR_MODEL_CAPABILITIES.items():
        # sonnet-5-5 stays unprobed so admit tests see CURSOR_MODEL_UNPROBED.
        if cap.probed_at is not None or model_id == "claude-sonnet-5-5":
            stamped[model_id] = cap
        else:
            stamped[model_id] = replace(cap, probed_at="test-probe-stamp")
    monkeypatch.setattr(cap_mod, "CURSOR_MODEL_CAPABILITIES", stamped)
    monkeypatch.setattr(cursor_capabilities, "CURSOR_MODEL_CAPABILITIES", stamped)


@pytest.fixture(autouse=True)
def _cursor_bus_default_is_test_double(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Block live agent-bus posts from ``client or CursorBusClient()`` defaults."""
    if skip_cursor_bus_hermetic_for_node(request.node.nodeid):
        return
    install_cursor_bus_hermetic(monkeypatch)


@pytest.fixture(autouse=True)
def house_watch_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    from implement_admission.house_testing import isolate_watch_dir

    return isolate_watch_dir(monkeypatch, tmp_path)


@pytest.fixture
def pinned_cdp_roles(monkeypatch: pytest.MonkeyPatch):
    from implement_admission.house_testing import pin_cdp_roles

    return pin_cdp_roles(monkeypatch)
