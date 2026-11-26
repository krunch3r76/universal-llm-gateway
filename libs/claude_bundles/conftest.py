"""Opt-in G1 registry isolation fixture for claude_bundles tests."""

from __future__ import annotations

from typing import NoReturn

import pytest

from claude_bundles.cdp_test_isolation import apply_g1_registry_isolation

_UNARMED_REASON = "g1 guard not armed: ledger criteria need the guarded invocation"


class _UnarmedLedger:
    """Ledger stand-in for a run with no armed guard: every read skips the node."""

    def _refuse(self, *_args: object) -> NoReturn:
        pytest.skip(_UNARMED_REASON)

    __len__ = __iter__ = __getitem__ = _refuse


class _UnarmedGuard:
    """Guard stand-in for an invocation that provides no ``g1_guard`` fixture.

    It enforces nothing and records nothing. It lets the seam patches of
    ``apply_g1_registry_isolation`` apply without an armed guard, and its
    ledger refuses every read so that no test can pass on an empty one.
    """

    def __init__(self) -> None:
        self._ledger = _UnarmedLedger()

    def ledger(self) -> _UnarmedLedger:
        return self._ledger

    def note_test_pid(self, _pid: int) -> None:
        return None

    def install_token(self) -> str:
        return ""


@pytest.fixture
def g1_registry_isolation(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch, tmp_path
):
    try:
        guard = request.getfixturevalue("g1_guard")
    except pytest.FixtureLookupError:
        guard = _UnarmedGuard()
    yield apply_g1_registry_isolation(monkeypatch, tmp_path, guard=guard)
