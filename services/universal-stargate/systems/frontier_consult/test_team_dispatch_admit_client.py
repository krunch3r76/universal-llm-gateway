"""Unit tests for run_team_dispatch_admit_sync (not conftest-stubbed)."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from systems.frontier_consult.admission import FrontierEndpointError
from systems.frontier_consult.team_dispatch_admit_client import run_team_dispatch_admit_sync


def _body(**fields: object) -> SimpleNamespace:
    return SimpleNamespace(**fields)


def _ok_response(content: dict) -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {
        "choices": [{"message": {"content": json.dumps(content)}}],
    }
    resp.raise_for_status = MagicMock()
    return resp


@pytest.mark.parametrize(
    ("status_code", "cause"),
    [(500, "unreachable"), (503, "unreachable")],
)
def test_unavailable_emits_once_before_raise_on_5xx(
    status_code: int, cause: str
) -> None:
    events: list[object] = []
    resp = MagicMock()
    resp.status_code = status_code
    client = MagicMock()
    client.post.return_value = resp
    client.__enter__ = MagicMock(return_value=client)
    client.__exit__ = MagicMock(return_value=False)

    with patch(
        "systems.frontier_consult.team_dispatch_admit_client.make_sync_client",
        return_value=client,
    ):
        with pytest.raises(FrontierEndpointError) as exc:
            run_team_dispatch_admit_sync(
                body=_body(model="cursor/grok-4.7"),
                request_id="req-5xx",
                event_publisher=events.append,
            )
    assert exc.value.code == "team_dispatch_admit_unavailable"
    assert len(events) == 1
    assert events[0].signal == "frontier.admit.unavailable"
    assert events[0].payload["cause"] == cause


def test_idle_timeout_emits_unavailable() -> None:
    events: list[object] = []
    client = MagicMock()
    client.post.side_effect = TimeoutError("read timed out")
    client.__enter__ = MagicMock(return_value=client)
    client.__exit__ = MagicMock(return_value=False)

    with patch(
        "systems.frontier_consult.team_dispatch_admit_client.make_sync_client",
        return_value=client,
    ):
        with pytest.raises(FrontierEndpointError):
            run_team_dispatch_admit_sync(
                body=_body(model="cursor/grok-4.7"),
                request_id="req-timeout",
                event_publisher=events.append,
            )
    assert len(events) == 1
    assert events[0].payload["cause"] == "idle_timeout"


def test_malformed_content_emits_unavailable() -> None:
    events: list[object] = []
    client = MagicMock()
    client.post.return_value = _ok_response({"admitted": True})
    client.__enter__ = MagicMock(return_value=client)
    client.__exit__ = MagicMock(return_value=False)

    with patch(
        "systems.frontier_consult.team_dispatch_admit_client.make_sync_client",
        return_value=client,
    ):
        with pytest.raises(FrontierEndpointError):
            run_team_dispatch_admit_sync(
                body=_body(model="cursor/grok-4.7"),
                request_id="req-malformed",
                event_publisher=events.append,
            )
    assert len(events) == 1
    assert events[0].payload["cause"] == "malformed"


def test_decided_emits_once_on_accept() -> None:
    events: list[object] = []
    plan = {
        "admitted": True,
        "backend": "cursor_sdk",
        "model": "cursor/grok-4.7",
    }
    client = MagicMock()
    client.post.return_value = _ok_response(plan)
    client.__enter__ = MagicMock(return_value=client)
    client.__exit__ = MagicMock(return_value=False)

    with patch(
        "systems.frontier_consult.team_dispatch_admit_client.make_sync_client",
        return_value=client,
    ):
        out = run_team_dispatch_admit_sync(
            body=_body(model="cursor/grok-4.7"),
            request_id="req-ok",
            event_publisher=events.append,
        )
    assert out == plan
    assert len(events) == 1
    assert events[0].signal == "frontier.admit.decided"


def test_rejected_emits_once_on_reject_envelope() -> None:
    events: list[object] = []
    plan = {
        "admitted": False,
        "code": "role_or_seat_required",
        "field": "role",
        "status_code": 422,
        "reason": "missing",
    }
    client = MagicMock()
    client.post.return_value = _ok_response(plan)
    client.__enter__ = MagicMock(return_value=client)
    client.__exit__ = MagicMock(return_value=False)

    with patch(
        "systems.frontier_consult.team_dispatch_admit_client.make_sync_client",
        return_value=client,
    ):
        with pytest.raises(FrontierEndpointError):
            run_team_dispatch_admit_sync(
                body=_body(),
                request_id="req-reject",
                event_publisher=events.append,
            )
    assert len(events) == 1
    assert events[0].signal == "frontier.admit.rejected"
