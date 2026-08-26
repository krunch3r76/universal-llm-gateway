"""Runner converse envelope + skip deregister on unverifiable (a:30678 / a:30679)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from claude_bundles.project_ask import ProjectAskResult

from cdp_ask.models import SubmitProjectAskRequest
from cdp_ask.runner import _persist_session_address, run_execution

pytestmark = pytest.mark.offline


def test_persist_session_address_skips_new(monkeypatch: pytest.MonkeyPatch) -> None:
    binds: list[str] = []
    monkeypatch.setattr(
        "cdp_ask.runner.cdp_registry.bind_session_address",
        lambda _rid, chat_url, execution_id=None: binds.append(chat_url),
    )
    _persist_session_address("reg-1", "https://claude.ai/new", execution_id="e1")
    _persist_session_address(
        "reg-1",
        "https://claude.ai/cowork/cse_abc",
        execution_id="e1",
    )
    assert binds == ["https://claude.ai/cowork/cse_abc"]


def _fail_result(*, url: str, error: str) -> ProjectAskResult:
    return ProjectAskResult(
        ok=False,
        body="",
        url=url,
        project_uuid="",
        project_url="https://claude.ai/new",
        model={"ok": False},
        body_len=0,
        delete_after=None,
        error=error,
    )


@pytest.mark.asyncio
async def test_run_execution_preserves_inner_error_and_skips_deregister() -> None:
    reg = MagicMock()
    reg.registration_id = "reg-review"
    reg.cdp_url = "http://127.0.0.1:9222"
    deregister = MagicMock()
    cse = "https://claude.ai/cowork/cse_abc"
    with (
        patch("cdp_ask.runner.bind_execution_lane", return_value=reg),
        patch(
            "cdp_ask.runner.run_project_conversation",
            new=AsyncMock(
                return_value=[
                    _fail_result(url=cse, error="model select failed: picker")
                ]
            ),
        ),
        patch("cdp_ask.runner.deregister_on_exit", deregister),
        patch("cdp_ask.runner.registration_has_wake_debt", return_value=False),
        patch("cdp_ask.runner.cdp_registry.bind_session_address"),
        patch("cdp_ask.runner._wake_debt_extras", return_value={}),
    ):
        payload = await run_execution(
            SubmitProjectAskRequest(
                prompt_text="ping",
                converse=True,
                no_project_uuid=True,
                purpose="review",
            ),
            execution_id="sat-1",
            abort_check=AsyncMock(return_value=False),
        )
    assert payload["ok"] is False
    assert payload["error"] == "model select failed: picker"
    assert payload["stall_stage"] == "observer_unverified"
    assert payload["url"] == cse
    deregister.assert_not_called()
