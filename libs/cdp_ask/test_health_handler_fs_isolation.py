"""``/health`` must stay within budget while handlers wait on harvest-root I/O."""

from __future__ import annotations

import threading
import time

import pytest
from deploy_identity.tree_state import (
    _reset_tree_state_cache_for_tests,
    note_tree_state,
)
from fastapi.testclient import TestClient

from cdp_ask.app import create_app
from cdp_ask.models import FollowupProjectAskResponse
from cdp_ask.runner import (
    HarvestRootHealth,
    _reset_harvest_root_health_cache_for_tests,
    note_harvest_root_health,
)
from cdp_ask.standing_pins import (
    _reset_health_projection_cache_for_tests,
)
from cdp_ask.test_health_tree_state import (
    _disable_health_background_refresh,
    _stub_startup_health_refresh,
)


@pytest.mark.offline
def test_health_responsive_while_verify_harvest_root_blocks_in_handler(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Blocking ``verify_harvest_root`` in a worker thread must not stall ``/health``."""
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    _disable_health_background_refresh(monkeypatch)
    _reset_tree_state_cache_for_tests()
    _reset_harvest_root_health_cache_for_tests()
    _reset_health_projection_cache_for_tests()
    note_tree_state("clean")
    note_harvest_root_health(HarvestRootHealth(str(tmp_path), True))
    _stub_startup_health_refresh(monkeypatch, str(tmp_path), harvest_ok=True)

    release = threading.Event()
    verify_entered = threading.Event()
    verify_calls = {"n": 0}

    def _verify_harvest_root() -> object:
        verify_calls["n"] += 1
        if verify_calls["n"] == 1:
            return tmp_path
        verify_entered.set()
        release.wait(timeout=10)
        return tmp_path

    async def _stub_followup(_req: object, _store: object) -> FollowupProjectAskResponse:
        return FollowupProjectAskResponse(ok=False, error="stub")

    monkeypatch.setattr("cdp_ask.app.verify_harvest_root", _verify_harvest_root)
    monkeypatch.setattr("cdp_ask.app.execute_followup", _stub_followup)

    app = create_app()
    with TestClient(app) as client:
        handler_error: list[Exception] = []

        def _run_followup() -> None:
            try:
                client.post(
                    "/v1/project-ask/followups",
                    json={"prompt_text": "x", "chat_url": "https://claude.ai/cowork/cse_test"},
                )
            except Exception as exc:  # noqa: BLE001 — surface in main thread
                handler_error.append(exc)

        worker = threading.Thread(target=_run_followup, daemon=True)
        worker.start()
        assert verify_entered.wait(timeout=2.0), "handler never entered verify_harvest_root"

        started = time.monotonic()
        payload = client.get("/health").json()
        elapsed = time.monotonic() - started

        release.set()
        worker.join(timeout=5.0)

    assert not handler_error
    assert elapsed < 2.0
    assert payload["status"] == "ok"
