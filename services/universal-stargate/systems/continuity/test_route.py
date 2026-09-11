"""Route tests for continuity checkpoint and tape-read."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.offline


def test_checkpoint_request_model_fields() -> None:
    from systems.continuity.models import CheckpointAccepted, CheckpointRequest

    req = CheckpointRequest(
        thread="10223",
        surface="cursor",
        from_agent="cursor",
        transcript_id="550e8400-e29b-41d4-a716-446655440000",
        pre_consolidate=True,
    )
    assert req.thread == "10223"
    assert req.surface == "cursor"

    accepted = CheckpointAccepted(
        execution_id="exec-1",
        pipeline="continuity-checkpoint-v1",
        thread="10223",
        started_at="2026-09-09T00:00:00Z",
        poll_hint={"tool": "agent_bus_read"},
    )
    assert accepted.status == "running"


def test_tape_read_envelope_carries_cells() -> None:
    from continuity_tape.messages import ContinuityMessagesEnvelope

    from systems.continuity.tape_read import build_envelope_from_tape

    tape = {
        "messages": [{"role": "user", "content": "hi", "turn_index": 1}],
        "index": [],
        "cells": [
            {
                "cp_ordinal": 1,
                "transcript_id": "uuid",
                "turn_lo": 0,
                "turn_hi": 1,
                "bus_turn_id": 10,
            }
        ],
        "segments": [{"session_id": "s1", "transcript_id": "uuid"}],
        "open_line": {"turn_count": 1, "truncated": False},
        "meta": {"messages_sha256": "abc"},
    }
    envelope = build_envelope_from_tape(
        tape,
        request={"thread": "10223", "scope": "window", "include_extras": True},
        caller_agent="cursor",
    )
    assert isinstance(envelope, ContinuityMessagesEnvelope)
    assert len(envelope.cells) == 1
    assert envelope.meta.checkpoint_turns == [10]


def _import_route_module(*, with_dispatch: bool = False):
    """Import continuity route without pulling the full proxy app (circular import)."""
    import sys
    import types

    if "systems.proxy.dependencies" not in sys.modules:
        proxy_pkg = types.ModuleType("systems.proxy")
        proxy_deps = types.ModuleType("systems.proxy.dependencies")
        proxy_deps.get_auth_dependency = lambda: lambda: {}  # noqa: E731
        proxy_pkg.dependencies = proxy_deps
        sys.modules["systems.proxy"] = proxy_pkg
        sys.modules["systems.proxy.dependencies"] = proxy_deps

    if with_dispatch:

        class _FakeDispatchRequest:
            def __init__(self, **kwargs: object) -> None:
                self._kwargs = kwargs

            def model_dump(self, *, exclude_none: bool = True) -> dict:
                _ = exclude_none
                return dict(self._kwargs)

        for name in (
            "systems.proxy.routers",
            "systems.proxy.routers.api",
            "systems.proxy.routers.api.pipelines_dispatch",
        ):
            if name not in sys.modules:
                sys.modules[name] = types.ModuleType(name)
        dispatch_mod = sys.modules["systems.proxy.routers.api.pipelines_dispatch"]
        dispatch_mod.DispatchRequest = _FakeDispatchRequest

    import importlib

    return importlib.import_module("systems.continuity.route")


@asynccontextmanager
async def _mock_dispatch_client(*_args, **_kwargs):
    client = MagicMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 202
    mock_resp.json.return_value = {
        "execution_id": "exec-claude-ai",
        "started_at": "2026-09-10T00:00:00Z",
    }
    client.post = AsyncMock(return_value=mock_resp)
    yield client


@pytest.mark.asyncio
async def test_checkpoint_claude_ai_requires_chat_url() -> None:
    from systems.continuity.models import CheckpointRequest

    route_mod = _import_route_module()
    body = CheckpointRequest(
        thread="10479",
        surface="claude_ai",
        from_agent="cursor",
    )
    resp = await route_mod.continuity_checkpoint(body, current_user={"agent": "cursor"})
    assert resp.status_code == 422
    payload = json.loads(resp.body)
    assert payload["error"]["code"] == "checkpoint.chat_url_required"


@pytest.mark.asyncio
async def test_checkpoint_claude_ai_admits_with_chat_url() -> None:
    from systems.continuity.models import CheckpointRequest

    route_mod = _import_route_module(with_dispatch=True)
    body = CheckpointRequest(
        thread="10479",
        surface="claude_ai",
        from_agent="cursor",
        chat_url="https://claude.ai/cowork/cse_0181xcjbYP83D8VdBopSyiLs",
    )
    with (
        patch.object(route_mod, "make_async_client", _mock_dispatch_client),
        patch("agent_bus_store.db.get_thread_turn_count", return_value=5),
    ):
        resp = await route_mod.continuity_checkpoint(
            body,
            current_user={"agent": "cursor"},
        )
    assert resp.status_code == 202
    payload = json.loads(resp.body)
    assert payload["execution_id"] == "exec-claude-ai"
    assert payload["pipeline"] == "continuity-checkpoint-v1"
