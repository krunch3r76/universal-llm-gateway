"""G3 AC17: non-hop CDP escalation commissions generate job=freeform.

Breaks when a cursor-auto job generate refuses (answer, ask, verify,
execute, propagate, seed, recon) is forwarded as the generate job.
Stargate down is a transport miss and is not this assertion.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_REFUSED_ON_GENERATE = (
    "answer",
    "ask",
    "verify",
    "execute",
    "propagate",
    "seed",
    "recon",
)


@pytest.mark.asyncio
@pytest.mark.parametrize("cursor_auto_job", _REFUSED_ON_GENERATE)
async def test_nonhop_escalation_commissions_freeform(cursor_auto_job: str) -> None:
    from services.git_integration_worker.cdp_escalation import commission_cdp_escalation

    mock_resp = MagicMock()
    mock_resp.status_code = 202
    mock_resp.json.return_value = {"execution_id": "exec-ac17", "status": "started"}
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch(
        "services.git_integration_worker.cdp_escalation.make_async_client",
        return_value=mock_client,
    ):
        result = await commission_cdp_escalation(
            model="cdp/opus-5.5",
            prompt="answer the question",
            thread_id="14394",
            cursor_auto_job=cursor_auto_job,
            mission_kind=None,
            stargate_url="http://stargate.test",
        )

    assert result["ok"] is True
    assert 200 <= result["status_code"] < 300
    assert result["execution_id"] == "exec-ac17"
    posted = mock_client.post.await_args.kwargs["json"]
    assert posted["op"] == "generate"
    assert posted["job"] == "freeform"
    assert posted["job"] != cursor_auto_job
    assert cursor_auto_job not in posted.values()
