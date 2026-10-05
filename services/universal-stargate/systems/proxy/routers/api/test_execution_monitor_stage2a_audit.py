"""Stage 2a audit holes (AC2, AC3, AC8, AC9) — execution monitor ladder."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from systems.frontier_consult.cdp_generate_inflight_ledger import InflightLeg
from systems.proxy.dependencies import get_auth_dependency, get_proxy
from systems.proxy.routers.api import (
    execution_authority_read,
    execution_monitor,
    executions,
)
from systems.proxy.routers.api.execution_authority_read import AuthorityReadResult

pytestmark = pytest.mark.offline

_FIVE_SOURCE_NAMES = (
    "pipeline_tracker",
    "pipeline_dispatch_journal",
    "cdp_registry.execution_state",
    "cdp_ask.execution_store",
    "thread_dispatch_links",
)


def _assert_five_source_miss(
    sources: list[dict[str, Any]],
    *,
    authority_reason: str = "miss",
    authority_degraded: bool = False,
    bus_non_authoritative: bool = True,
) -> None:
    assert len(sources) == 5
    by_name = {row["source"]: row for row in sources}
    assert set(by_name) == set(_FIVE_SOURCE_NAMES)
    assert by_name["pipeline_tracker"] == {
        "source": "pipeline_tracker",
        "hit": False,
        "reason": "miss",
    }
    assert by_name["pipeline_dispatch_journal"] == {
        "source": "pipeline_dispatch_journal",
        "hit": False,
        "reason": "miss",
    }
    authority = by_name["cdp_registry.execution_state"]
    assert authority["hit"] is False
    assert authority["reason"] == authority_reason
    assert bool(authority.get("degraded")) is authority_degraded
    assert by_name["cdp_ask.execution_store"]["hit"] is False
    bus = by_name["thread_dispatch_links"]
    assert bus["hit"] is False
    assert bus["reason"] == ("non_authoritative" if bus_non_authoritative else "miss")


def _tracker_stub() -> MagicMock:
    tracker = MagicMock()
    tracker.get = MagicMock(return_value=None)
    tracker.wait_for_terminal = AsyncMock(return_value=None)
    tracker._agent_bus_url = "unix:///tmp/agent-bus.sock"
    tracker._agent_bus_token = "test-token"
    return tracker


def _executions_client(
    tracker: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> TestClient:
    monkeypatch.setattr(executions, "_get_tracker", lambda _proxy: tracker)
    monkeypatch.setattr(execution_monitor, "fetch_record", AsyncMock(return_value=None))
    app = FastAPI()
    app.include_router(executions.router)
    app.dependency_overrides[get_proxy] = lambda: MagicMock()
    app.dependency_overrides[get_auth_dependency] = lambda: {}
    return TestClient(app)


def test_sources_consulted_miss_preserves_satellite_store_reason() -> None:
    """Honest miss: satellite sources_consulted rows survive into 404 data."""
    sources = execution_monitor.sources_consulted_miss(
        authority_reason="http_503",
        authority_degraded=True,
        authority_payload={
            "sources_consulted": [
                {
                    "source": "cdp_registry.execution_state",
                    "hit": False,
                    "reason": "no_row",
                },
                {
                    "source": "cdp_ask.execution_store",
                    "hit": False,
                    "reason": "miss",
                },
            ]
        },
        bus_link_reason="non_authoritative",
    )
    by_name = {row["source"]: row for row in sources}
    assert by_name["cdp_registry.execution_state"]["reason"] == "no_row"
    assert by_name["cdp_registry.execution_state"]["degraded"] is True
    assert by_name["cdp_ask.execution_store"]["reason"] == "miss"


@pytest.mark.asyncio
async def test_ac2_authority_streaming_when_tracker_journal_bus_miss(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CDP-shaped id: satellite streaming authority wins; no bus_thread recovery."""
    execution_id = "67aae3ee-ff0b-4e63-bfad-de87ceac2f93"
    tracker = _tracker_stub()

    async def _authority(_eid: str) -> AuthorityReadResult:
        return AuthorityReadResult(
            ok=True,
            payload={
                "execution_state": {
                    "execution_id": execution_id,
                    "state": "streaming",
                    "updated_at": 1_700_000_000.0,
                    "holder_pid": 42,
                },
                "execution_state_freshness": "live",
                "as_of": "2026-01-01T00:00:00Z",
            },
        )

    monkeypatch.setattr(execution_monitor, "read_execution_authority", _authority)
    monkeypatch.setattr(
        execution_monitor,
        "recover_execution_from_bus_thread",
        AsyncMock(return_value=None),
    )

    status, body = await execution_monitor.resolve_execution_monitor(
        execution_id,
        tracker=tracker,
        wait_seconds=0.0,
        event_bus=None,
    )
    assert status == 200
    assert body is not None
    assert body["status"] == "running"
    assert body["state"] == "streaming"
    assert body["source"] == "cdp_registry.execution_state"
    assert "recovered_from" not in body.get("recovery", {})


def _fake_satellite_state_payload(
    execution_id: str,
    *,
    state: str,
) -> dict[str, Any]:
    return {
        "execution_state": {
            "execution_id": execution_id,
            "state": state,
            "updated_at": 1_700_000_000.0,
            "holder_pid": 42,
        },
        "execution_state_freshness": "live",
        "store": None,
        "as_of": "2026-01-01T00:00:00Z",
    }


def _patch_fake_satellite_state(
    monkeypatch: pytest.MonkeyPatch,
    execution_id: str,
    *,
    state: str,
    satellite_execution_id: str | None = None,
) -> None:
    """Wire relay + project-ask URL; optional inflight leg for id resolution."""
    monkeypatch.setattr(
        execution_authority_read,
        "project_ask_base_url",
        lambda: "http://127.0.0.1:9",
    )
    if satellite_execution_id is not None:

        def _read_leg(eid: str) -> InflightLeg | None:
            if eid != execution_id:
                return None
            return InflightLeg(
                execution_id=execution_id,
                request_id="req-ac2",
                satellite_execution_id=satellite_execution_id,
                thread_id="15068",
                pointer_turn=1,
                caller_agent="cursor",
                prompt_uri="cortex://test",
                model_id="cdp/fable-5.1",
                max_wall_s=600.0,
                admitted_at="2026-01-01T00:00:00Z",
                proof_emitted=False,
                delivered=False,
                abandoned=False,
            )

        monkeypatch.setattr(execution_authority_read, "read_inflight_leg", _read_leg)

    expected_path = f"/v1/project-ask/executions/{execution_id}/state"
    if satellite_execution_id:
        expected_path = (
            f"{expected_path}?satellite_execution_id={satellite_execution_id}"
        )
    payload = _fake_satellite_state_payload(execution_id, state=state)

    async def _relay(method: str, path: str, **_kwargs: Any) -> tuple[int, dict, str]:
        assert method == "GET"
        assert path == expected_path
        return 200, payload, "application/json"

    monkeypatch.setattr(execution_authority_read, "relay_async", _relay)


@pytest.mark.parametrize("state", ["seated", "streaming"])
def test_ac2_get_inflight_via_fake_satellite_state(
    monkeypatch: pytest.MonkeyPatch,
    state: str,
) -> None:
    """GET ladder hits relay /state; read_execution_authority is not stubbed."""
    execution_id = "0af05e2f-1111-4222-8333-444455556666"
    satellite_id = "67aae3ee-ff0b-4e63-bfad-de87ceac2f93"
    tracker = _tracker_stub()
    _patch_fake_satellite_state(
        monkeypatch,
        execution_id,
        state=state,
        satellite_execution_id=satellite_id,
    )
    monkeypatch.setattr(
        execution_monitor,
        "recover_execution_from_bus_thread",
        AsyncMock(
            side_effect=AssertionError(
                "bus_thread recovery must not run on authority hit"
            )
        ),
    )
    client = _executions_client(tracker, monkeypatch)

    response = client.get(f"/executions/{execution_id}")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "running"
    assert body["state"] == state
    assert body["source"] == "cdp_registry.execution_state"
    assert "recovered_from" not in body.get("recovery", {})


@pytest.mark.asyncio
async def test_ac3_authority_beats_store_when_both_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Resolvable registry row + store + bus fork still names authority source."""
    execution_id = "exec-ac3"
    tracker = _tracker_stub()

    async def _authority(_eid: str) -> AuthorityReadResult:
        return AuthorityReadResult(
            ok=True,
            payload={
                "execution_state": {
                    "execution_id": execution_id,
                    "state": "seated",
                    "updated_at": 1_700_000_000.0,
                    "holder_pid": 7,
                },
                "execution_state_freshness": "live",
                "store": {"execution_id": execution_id, "status": "running"},
                "as_of": "2026-01-01T00:00:00Z",
            },
        )

    monkeypatch.setattr(execution_monitor, "read_execution_authority", _authority)
    bus_recovered = {
        "status": "completed",
        "source": "thread_dispatch_links",
        "recovery": {"recovered_from": "bus_thread"},
    }
    monkeypatch.setattr(
        execution_monitor,
        "recover_execution_from_bus_thread",
        AsyncMock(return_value=bus_recovered),
    )

    status, body = await execution_monitor.resolve_execution_monitor(
        execution_id,
        tracker=tracker,
        wait_seconds=0.0,
        event_bus=None,
    )
    assert status == 200
    assert body is not None
    assert body["source"] == "cdp_registry.execution_state"
    assert body["state"] == "seated"
    assert "recovered_from" not in body.get("recovery", {})


@pytest.mark.asyncio
async def test_ac8_project_ask_unset_yields_degraded_miss_404(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _tracker_stub()

    async def _authority(_eid: str) -> AuthorityReadResult:
        return AuthorityReadResult(
            ok=False,
            payload=None,
            reason="project_ask_url_unset",
            degraded=True,
        )

    monkeypatch.setattr(execution_monitor, "read_execution_authority", _authority)
    monkeypatch.setattr(
        execution_monitor,
        "recover_execution_from_bus_thread",
        AsyncMock(return_value=None),
    )

    status, body = await execution_monitor.resolve_execution_monitor(
        "missing-exec",
        tracker=tracker,
        wait_seconds=0.0,
        event_bus=None,
    )
    assert status == 404
    assert body is not None
    sources = body["data"]["sources_consulted"]
    _assert_five_source_miss(
        sources,
        authority_reason="project_ask_url_unset",
        authority_degraded=True,
    )


@pytest.mark.asyncio
async def test_ac8_bus_recovery_marks_degraded_when_authority_unreachable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _tracker_stub()

    async def _authority(_eid: str) -> AuthorityReadResult:
        return AuthorityReadResult(
            ok=False,
            payload=None,
            reason="transport_error:connection refused",
            degraded=True,
        )

    monkeypatch.setattr(execution_monitor, "read_execution_authority", _authority)
    monkeypatch.setattr(
        execution_monitor,
        "recover_execution_from_bus_thread",
        AsyncMock(
            return_value={
                "status": "completed",
                "source": "thread_dispatch_links",
                "recovery": {
                    "recovered_from": "bus_thread",
                    "consulted": ["thread_dispatch_links"],
                },
            }
        ),
    )

    status, body = await execution_monitor.resolve_execution_monitor(
        "exec-bus-fallback",
        tracker=tracker,
        wait_seconds=0.0,
        event_bus=None,
    )
    assert status == 200
    assert body is not None
    assert body["source"] == "thread_dispatch_links"
    recovery = body["recovery"]
    assert recovery.get("degraded") is True
    assert recovery.get("source_attempted") == "cdp_registry.execution_state"
    assert recovery.get("recovered_from") == "bus_thread"


def test_ac9_get_404_lists_five_sources_with_miss_reasons(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _tracker_stub()

    async def _authority(_eid: str) -> AuthorityReadResult:
        return AuthorityReadResult(
            ok=False, payload=None, reason="miss", degraded=False
        )

    monkeypatch.setattr(execution_monitor, "read_execution_authority", _authority)
    monkeypatch.setattr(
        execution_monitor,
        "recover_execution_from_bus_thread",
        AsyncMock(return_value=None),
    )
    client = _executions_client(tracker, monkeypatch)

    response = client.get("/executions/unknown-exec")
    assert response.status_code == 404
    err = response.json()["error"]
    assert err["code"] == "execution_id_expired_or_unknown"
    _assert_five_source_miss(err["data"]["sources_consulted"])


@pytest.mark.asyncio
async def test_ac8_authority_unreachable_event_when_event_bus_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Degraded authority miss emits pipeline.execution.authority_unreachable."""
    tracker = _tracker_stub()
    published: list[Any] = []

    class _Bus:
        async def publish_nowait(self, event: Any) -> None:
            published.append(event)

    async def _authority(_eid: str) -> AuthorityReadResult:
        return AuthorityReadResult(
            ok=False,
            payload=None,
            reason="project_ask_url_unset",
            degraded=True,
        )

    monkeypatch.setattr(execution_monitor, "read_execution_authority", _authority)
    monkeypatch.setattr(
        execution_monitor,
        "recover_execution_from_bus_thread",
        AsyncMock(return_value=None),
    )

    status, _body = await execution_monitor.resolve_execution_monitor(
        "exec-event",
        tracker=tracker,
        wait_seconds=0.0,
        event_bus=_Bus(),
    )
    assert status == 404
    await asyncio.sleep(0)
    assert len(published) == 1
    event = published[0]
    assert event.signal == "pipeline.execution.authority_unreachable"
    assert event.payload.get("execution_id") == "exec-event"


def test_ac9_delete_cancel_wait_miss_lists_five_sources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DELETE cancel path: wait_for_terminal None → 404 with sources_consulted."""
    from fastapi import FastAPI

    from systems.proxy.dependencies import get_auth_dependency, get_proxy

    tracker = _tracker_stub()
    running = MagicMock()
    running.status = "running"
    running.pipeline = "frontier-dispatch"
    tracker.get = MagicMock(return_value=running)
    tracker.wait_for_terminal = AsyncMock(return_value=None)
    tracker.fail_execution = MagicMock()

    async def _authority(_eid: str) -> AuthorityReadResult:
        return AuthorityReadResult(
            ok=False, payload=None, reason="miss", degraded=False
        )

    monkeypatch.setattr(executions, "_get_tracker", lambda _proxy: tracker)
    monkeypatch.setattr(execution_monitor, "fetch_record", AsyncMock(return_value=None))
    monkeypatch.setattr(execution_monitor, "read_execution_authority", _authority)
    monkeypatch.setattr(
        execution_monitor,
        "recover_execution_from_bus_thread",
        AsyncMock(return_value=None),
    )

    app = FastAPI()
    app.state.pipeline_task_index = {}
    app.include_router(executions.router)
    proxy = MagicMock()
    proxy.event_bus = None
    app.dependency_overrides[get_proxy] = lambda: proxy
    app.dependency_overrides[get_auth_dependency] = lambda: {}

    response = TestClient(app).delete("/executions/exec-cancel-timeout")
    assert response.status_code == 404
    err = response.json()["error"]
    _assert_five_source_miss(err["data"]["sources_consulted"])


def test_ac9_delete_404_lists_same_five_sources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _tracker_stub()

    async def _authority(_eid: str) -> AuthorityReadResult:
        return AuthorityReadResult(
            ok=False, payload=None, reason="miss", degraded=False
        )

    monkeypatch.setattr(execution_monitor, "read_execution_authority", _authority)
    monkeypatch.setattr(
        execution_monitor,
        "recover_execution_from_bus_thread",
        AsyncMock(return_value=None),
    )
    client = _executions_client(tracker, monkeypatch)

    response = client.delete("/executions/unknown-exec")
    assert response.status_code == 404
    err = response.json()["error"]
    assert err["code"] == "execution_id_expired_or_unknown"
    _assert_five_source_miss(err["data"]["sources_consulted"])
