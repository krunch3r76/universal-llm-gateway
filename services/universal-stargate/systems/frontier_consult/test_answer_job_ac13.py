"""G3 AC13 generate half: job=answer is unknown on Stargate generate intake.

Breaks when generate intake admits answer, or returns a reason other than
job_unknown, or omits dispatch.job.refused.
"""

from __future__ import annotations

import json

import pytest
from starlette.responses import Response

from systems.frontier_consult.route import TeamDispatchGenerateBody, team_dispatch


@pytest.mark.asyncio
async def test_generate_job_answer_is_job_unknown() -> None:
    response = await team_dispatch(
        TeamDispatchGenerateBody.model_construct(
            op="generate",
            dispatch_thread_id="dt-ac13",
            prompt="hello",
            job="answer",
        ),
        Response(),
    )
    body = json.loads(response.body)
    assert response.status_code == 422
    assert body["error"]["code"] == "job_unknown"
    assert body["details"]["event"] == "dispatch.job.refused"
    assert body["details"]["reason"] == "job_unknown"
