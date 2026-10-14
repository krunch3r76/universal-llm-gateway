"""Isolate guarded-reexec tests from the cursor-sdk dispatch that runs them."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _no_caller_dispatch(monkeypatch: pytest.MonkeyPatch) -> None:
    # pytest under a cursor-sdk dispatch inherits CURSOR_SDK_DISPATCH_ID; the
    # occupant guard would count the test runner as a GIW occupant everywhere.
    monkeypatch.delenv("CURSOR_SDK_DISPATCH_ID", raising=False)


@pytest.fixture(autouse=True)
def _guarded_reexec_live_path_isolation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Keep execute-path tests off live manage.sock and manage.armed.json (a:39206)."""
    from transport_utils import MANAGE_SOCKET

    import scripts.model_manager.guarded_manage_reexec.runner as runner_mod

    monkeypatch.setattr(runner_mod, "_publish_reexec", lambda _event: None)
    original = runner_mod.run_guarded_reexec

    def _wrapped(**kwargs: object) -> object:
        if kwargs.get("dry_run") is False:
            if kwargs.get("armed_record_path") is None:
                kwargs["armed_record_path"] = tmp_path / "manage.armed.json"
            if kwargs.get("sock_path", MANAGE_SOCKET) == MANAGE_SOCKET:
                kwargs["sock_path"] = str(tmp_path / "manage.sock")
        return original(**kwargs)

    monkeypatch.setattr(runner_mod, "run_guarded_reexec", _wrapped)
