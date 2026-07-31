"""Route-level tests for capability card error wire translation."""

from __future__ import annotations

import json
import sys
import types
from unittest.mock import MagicMock

import pytest
from fastapi import Response
from fastapi.responses import JSONResponse

from .route import (
    FrontierDispatchGenerateBody,
    _dispatch,
    frontier_dispatch,
)
from .service import FrontierGenerateRequest

_DISPATCH_THREAD = "test-dispatch-thread"


def _mock_get_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    mock_proxy = MagicMock()
    mock_proxy.event_bus = None
    fake_deps = types.ModuleType("systems.proxy.dependencies")
    fake_deps.get_proxy = lambda: mock_proxy  # type: ignore[attr-defined]
    if "systems.proxy" not in sys.modules:
        monkeypatch.setitem(
            sys.modules, "systems.proxy", types.ModuleType("systems.proxy")
        )
    monkeypatch.setitem(sys.modules, "systems.proxy.dependencies", fake_deps)


@pytest.mark.asyncio
async def test_dispatch_uncarded_model_returns_422_not_500(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mock_get_proxy(monkeypatch)

    req = FrontierGenerateRequest(
        messages=[{"role": "user", "content": "x"}],
        role="reviewer",
        dispatch_thread_id=_DISPATCH_THREAD,
        model="openai/gpt-4o",
        op="generate",
    )
    result = await _dispatch(req, Response())

    assert isinstance(result, JSONResponse)
    assert result.status_code == 422
    payload = json.loads(result.body)
    assert payload["error"]["code"] == "capability_card_missing"
    assert payload["field"] == "model"
    assert payload["details"]["model"] == "openai/gpt-4o"
    assert payload["details"]["capability_field"]
    assert "capabilitycarderror" not in json.dumps(payload).lower()


@pytest.mark.asyncio
async def test_frontier_dispatch_no_role_uncarded_returns_422(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mock_get_proxy(monkeypatch)

    body = FrontierDispatchGenerateBody(
        op="generate",
        model="openai/gpt-4o",
        messages=[{"role": "user", "content": "x"}],
    )
    result = await frontier_dispatch(body, Response())

    assert isinstance(result, JSONResponse)
    assert result.status_code == 422
    payload = json.loads(result.body)
    assert payload["error"]["code"] == "capability_card_missing"
    assert payload["field"] == "model"
