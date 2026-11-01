"""MCP pre-validation for role-less impresario critic generate."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from implement_admission.impresario_critic_admit import cloud_impresario_critic_models
from tools.frontier import register_frontier_tools


class _Recorder:
    def __init__(self) -> None:
        self.functions: dict[str, Any] = {}

    def tool(self, **_kwargs: Any) -> Any:
        def decorator(fn: Any) -> Any:
            self.functions[fn.__name__] = fn
            return fn

        return decorator


def _team_dispatch_tool() -> Any:
    recorder = _Recorder()
    register_frontier_tools(recorder)  # type: ignore[arg-type]
    return recorder.functions["team_dispatch"]


pytestmark = pytest.mark.offline


@pytest.mark.parametrize("model", cloud_impresario_critic_models())
@pytest.mark.asyncio
async def test_mcp_prevalidation_passes_cloud_critics(model: str) -> None:
    fn = _team_dispatch_tool()
    relay = AsyncMock(return_value={"execution_id": "exec-test", "status": "queued"})
    with patch("tools.frontier._relay", relay):
        result = await fn(
            op="generate",
            model=model,
            contract="code-review",
            dispatch_thread_id="dt-roleless-critic",
            prompt="bounded review",
        )
    assert result.get("field") != "role", result
    relay.assert_called_once()


@pytest.mark.asyncio
async def test_mcp_prevalidation_freeform_still_requires_role_seat() -> None:
    fn = _team_dispatch_tool()
    result = await fn(
        op="generate",
        model="anthropic/claude-sonnet-5-5",
        contract="freeform",
        dispatch_thread_id="dt-freeform",
        prompt="edit",
    )
    assert result.get("error", {}).get("code") == "validation_error"
    assert result.get("field") == "role"
