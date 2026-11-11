"""Shared pytest fixtures for libs/ offline registry and display tests.

No GIW dead-port isolation runs under libs/ (that autouse lives in
services/universal-stargate/conftest.py); libs tests that reach a GIW caller
must patch the HTTP client or set GIT_INTEGRATION_WORKER_URL in the test.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

_PIN_ENV = "PYTEST_EVENTS_INGEST_SOCK_PIN"


def _pin_events_ingest_sock_off_live() -> None:
    """Point ``EVENTS_INGEST_SOCK`` at a fresh tmp path before any libs package imports.

    ``agent_bus_store.events.publisher``, ``cortex_store.event_publisher`` and
    ``mcp_events`` read the variable at import time and start a sender thread
    that connects at once (default ``/tmp/universal-protocol/events.sock``).
    ``libs/`` has no ``__init__.py``, so pytest loads this conftest before it
    imports ``bus_watch.conftest`` or ``agent_bus_store.conftest``, whose
    package ``__init__`` modules already pull a publisher in. A second call
    in the same process is a no-op.
    """
    pinned = os.environ.get(_PIN_ENV)
    if pinned and os.environ.get("EVENTS_INGEST_SOCK") == pinned:
        return
    pin_dir = tempfile.mkdtemp(prefix="pytest-events-ingest-")
    pinned = os.path.join(pin_dir, "events.sock")
    os.environ[_PIN_ENV] = pinned
    os.environ["EVENTS_INGEST_SOCK"] = pinned


_pin_events_ingest_sock_off_live()


@pytest.fixture(autouse=True)
def _x_display_unobserved_by_default(
    monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> None:
    """``count_x11_unix_clients`` → None unless the test opts into a live/injected count.

    Production mint on Jupiter reads ``/proc/net/unix``. Leaving that live in
    unit tests would refuse every ``register_lane`` when Xvfb is at cap.
    """
    monkeypatch.setenv("CDP_DISPLAY", ":2")
    if request.node.get_closest_marker("live_x_display"):
        return
    monkeypatch.setattr(
        "claude_bundles.x_display_capacity.count_x11_unix_clients",
        lambda display, proc_net_unix=None: None,
    )


@pytest.fixture(autouse=True)
def _pin_lanes_empty_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Host ``pins.toml`` must not change headroom math or port selection.

    Tests that cover pin reserve or pin-port exclusion replace ``_load_pin_lanes``.
    """
    monkeypatch.setattr(
        "claude_bundles.x_display_capacity._load_pin_lanes",
        lambda: {},
    )


@pytest.fixture(autouse=True)
def _cdp_accounts_config_test_map(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, request: pytest.FixtureRequest
) -> None:
    """Map account-2 to ``:4`` for every ``offline`` test via a rewritten accounts YAML.

    ``CDP_ACCOUNTS_CONFIG_PATH`` wins over monkeypatching ``models.ACCOUNTS_CONFIG_PATH``
    (see ``models._accounts_config_path``). A test that needs the committed empty
    ``cdp_account_displays`` map must set ``CDP_ACCOUNTS_CONFIG_PATH`` to its own file.
    """
    if request.node.get_closest_marker("offline") is None:
        return
    repo_root = Path(__file__).resolve().parent.parent
    src = repo_root / "config" / "cdp" / "accounts.yaml"
    text = src.read_text(encoding="utf-8")
    if "cdp_account_displays: {}" in text:
        text = text.replace(
            "cdp_account_displays: {}",
            'cdp_account_displays:\n  account-2: ":4"',
            1,
        )
    dest = tmp_path / "cdp-accounts.yaml"
    dest.write_text(text, encoding="utf-8")
    monkeypatch.setenv("CDP_ACCOUNTS_CONFIG_PATH", str(dest))


@pytest.fixture(autouse=True)
def _cdp_registry_seat_authority_for_offline_tests(
    monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> None:
    """Hermetic registry tests bind seats against tmp dirs — act as authority."""
    if request.node.get_closest_marker("offline") is None:
        return
    monkeypatch.setenv("CDP_REGISTRY_SEAT_AUTHORITY", "1")
