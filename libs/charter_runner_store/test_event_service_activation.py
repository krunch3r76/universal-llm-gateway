"""event_service live-but-unattributed: /health probe shape vs close-path identity.

Production settle passes ``default_probe`` by identity, so ``settle_open_row``
uses ``proof_observed`` (not the test-lambda ``proof_matches_row`` shortcut).
event_service ``GET /health`` must carry an identifier-class field or a live
equal-ref restart stays ``activation_unattributed``.
"""

from __future__ import annotations

import os
import time
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import patch

import pytest
from event_store.query import create_query_router
from event_store.store import EventStore
from fastapi import FastAPI
from fastapi.testclient import TestClient
from implement_admission.propagation_row import PropagationRow

from charter_runner_store.propagation_ledger import list_open_rows, upsert_open_rows
from charter_runner_store.propagation_liveness import CodeRefLiveness
from charter_runner_store.propagation_terminal import default_probe, settle_open_row
from charter_runner_store.propagation_validation import current_validation
from services.git_integration_worker.cursor_auto.propagation_probe import (
    strong_process_identity,
)

_LIVE_SHA = "ff27ed04b4026bc29e93efdb2edbddf3d8935f25"


class _StubIngest:
    def get_metrics(self) -> dict[str, int]:
        return {
            "events_ingested": 2,
            "events_dropped_publish": 0,
            "queue_size": 0,
        }


def _live_shaped_health(*, code_version: str, pid: int | None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "status": "ok",
        "subscribers": 18,
        "events_ingested": 2,
        "events_dropped_publish": 0,
        "queue_size": 0,
        "code_version": code_version,
    }
    if pid is not None:
        payload["pid"] = pid
    return payload


def _open_event_service_row(code_ref: str) -> str:
    upsert_open_rows(
        [
            PropagationRow(
                service="event_service",
                code_ref=code_ref,
                proof_class="process_live",
                action="sync_restart",
            )
        ]
    )
    return list_open_rows()[0].row_id


def _live_yes(
    monkeypatch: pytest.MonkeyPatch, sha: str, observation: dict[str, Any]
) -> None:
    monkeypatch.setattr(
        "charter_runner_store.propagation_validation.close.observe_code_ref_live",
        lambda service, code_ref: CodeRefLiveness(
            answer="yes",
            service=service,
            code_ref=code_ref,
            observed_code_version=sha,
            relation="equal",
            observation=observation,
            reason="test",
        ),
    )


@pytest.mark.offline
def test_live_event_service_health_without_pid_stays_unattributed(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """14:14 specimen: code_version equal, no identifier field, default_probe arm."""
    monkeypatch.setenv("CHARTER_RUNNER_DATA_DIR", str(tmp_path))
    payload = _live_shaped_health(code_version=_LIVE_SHA, pid=None)
    assert strong_process_identity(payload) is False
    row_id = _open_event_service_row(_LIVE_SHA)
    with patch(
        "charter_runner_store.propagation_terminal._probe_for_projection",
        lambda _row: payload,
    ):
        result = settle_open_row(
            list_open_rows()[0],
            default_probe,
            defer_if_unreachable=True,
            settle_not_before_monotonic=time.monotonic() - 30.0,
        )
    assert result.outcome != "closed"
    assert list_open_rows()[0].row_id == row_id
    _live_yes(monkeypatch, _LIVE_SHA, payload)
    attributed = current_validation("event_service", _LIVE_SHA)
    assert attributed["verdict"] == "activation_unattributed"
    assert attributed["activation"] is None


@pytest.mark.offline
def test_event_service_health_pid_settles_and_attributes(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Force live /health specimen through default_probe; attribution must follow."""
    store = EventStore(":memory:")

    @asynccontextmanager
    async def _lifespan(_app: FastAPI) -> Any:
        await store.open()
        yield
        await store.close()

    app = FastAPI(lifespan=_lifespan)
    app.include_router(
        create_query_router(store, _StubIngest(), set())  # type: ignore[arg-type]
    )
    with TestClient(app) as client:
        resp = client.get("/health")
        assert resp.status_code == 200
        payload = resp.json()

    assert payload["pid"] == os.getpid()
    assert strong_process_identity(payload) is True
    sha = str(payload["code_version"])
    monkeypatch.setenv("CHARTER_RUNNER_DATA_DIR", str(tmp_path))
    row_id = _open_event_service_row(sha)
    with patch(
        "charter_runner_store.propagation_terminal._probe_for_projection",
        lambda _row: payload,
    ):
        result = settle_open_row(
            list_open_rows()[0],
            default_probe,
            defer_if_unreachable=True,
            settle_not_before_monotonic=time.monotonic() - 30.0,
        )
    assert result.outcome == "closed"
    assert list_open_rows() == []
    _live_yes(monkeypatch, sha, payload)
    attributed = current_validation("event_service", sha)
    assert attributed["verdict"] == "running_committed_code"
    assert attributed["activation"] is not None
    assert attributed["activation"]["row_id"] == row_id
