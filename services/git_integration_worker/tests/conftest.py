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


@pytest.fixture(scope="session", autouse=True)
def _isolate_dispatch_ledger(tmp_path_factory: pytest.TempPathFactory):
    """Point ``DATA_DIR`` at a tmp dir for the whole GIW session.

    Without this, ``_ledger_path()`` falls back to ``~/.gateway`` and the suite
    writes live ledgers. Also pins ``CURSOR_SDK_DISPATCH_LEDGER`` so a dispatch
    shell export of the live path cannot bypass ``DATA_DIR``. Session scope
    because ``CursorDispatchLedger`` and ``SeatWriteLedger`` cache the resolved
    path on the singleton at first touch. Reset both after env pins so a
    collection-time instance cannot keep the live path and defeat the pytest
    refuse belt.
    """
    import os

    from services.git_integration_worker.cursor_dispatch_ledger import (
        CURSOR_SDK_DISPATCH_LEDGER_ENV,
        CursorDispatchLedger,
    )
    from services.git_integration_worker.seat_write_ledger import SeatWriteLedger

    data_dir = tmp_path_factory.mktemp("giw-data-dir")
    isolated_ledger = data_dir / "cursor-sdk-dispatch.db"
    prior_data_dir = os.environ.get("DATA_DIR")
    prior_dispatch_ledger = os.environ.get(CURSOR_SDK_DISPATCH_LEDGER_ENV)
    os.environ["DATA_DIR"] = str(data_dir)
    os.environ[CURSOR_SDK_DISPATCH_LEDGER_ENV] = str(isolated_ledger)
    SeatWriteLedger.reset_instance()
    CursorDispatchLedger._instance = None
    yield data_dir
    if prior_data_dir is None:
        os.environ.pop("DATA_DIR", None)
    else:
        os.environ["DATA_DIR"] = prior_data_dir
    if prior_dispatch_ledger is None:
        os.environ.pop(CURSOR_SDK_DISPATCH_LEDGER_ENV, None)
    else:
        os.environ[CURSOR_SDK_DISPATCH_LEDGER_ENV] = prior_dispatch_ledger
    SeatWriteLedger.reset_instance()
    CursorDispatchLedger._instance = None


@pytest.fixture(autouse=True)
def _pin_isolated_dispatch_ledger_per_test(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Override dispatch-shell ``CURSOR_SDK_DISPATCH_LEDGER`` with a per-test tmp ledger.

    Session ``_isolate_dispatch_ledger`` pins ``DATA_DIR`` for singleton hygiene; a
    live bridge export of ``CURSOR_SDK_DISPATCH_LEDGER`` still wins unless each test
    monkeypatches it away. Function scope keeps holder/ledger rows from leaking
    across tests.
    """
    from services.git_integration_worker.cursor_dispatch_ledger import (
        CURSOR_SDK_DISPATCH_LEDGER_ENV,
        CursorDispatchLedger,
    )
    from services.git_integration_worker.seat_write_ledger import SeatWriteLedger

    root = tmp_path / "giw-ledger-root"
    root.mkdir()
    monkeypatch.setenv("DATA_DIR", str(root))
    monkeypatch.setenv(
        CURSOR_SDK_DISPATCH_LEDGER_ENV, str(root / "cursor-sdk-dispatch.db")
    )
    SeatWriteLedger.reset_instance()
    CursorDispatchLedger._instance = None


@pytest.fixture(autouse=True)
def _cursor_card_probed_at_for_admit_tests(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stamp ``probed_at`` on card entries so admit-path tests stay hermetic."""
    from dataclasses import replace

    import cursor_capabilities
    import cursor_capabilities.cursor_capabilities as cap_mod

    stamped: dict[str, cap_mod.ModelCapability] = {}
    for model_id, cap in cap_mod.CURSOR_MODEL_CAPABILITIES.items():
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
