"""Isolate guarded-reexec tests from the cursor-sdk dispatch that runs them."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _no_caller_dispatch(monkeypatch: pytest.MonkeyPatch) -> None:
    # pytest under a cursor-sdk dispatch inherits CURSOR_SDK_DISPATCH_ID; the
    # occupant guard would count the test runner as a GIW occupant everywhere.
    monkeypatch.delenv("CURSOR_SDK_DISPATCH_ID", raising=False)
