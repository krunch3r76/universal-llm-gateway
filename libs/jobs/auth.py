"""Bearer authentication for the jobs satellite.

Mirrors the agent-bus token check against ``JOBS_TOKEN``. Missing and wrong
tokens are 401. An unset token is 500 ``jobs_token_unconfigured`` so a
misconfigured process cannot fail open. Surface membership is separate and
is not authentication.
"""

from __future__ import annotations

import os

from fastapi import Request

from jobs.errors import JobsError


async def require_token(request: Request) -> None:
    """Reject the request unless Authorization is the configured jobs bearer.

    Reads ``JOBS_TOKEN`` on each call so tests can change the environment
    without reimporting the module. Does not consult the journal.
    """
    expected = os.environ.get("JOBS_TOKEN", "")
    if not expected:
        raise JobsError(
            "jobs_token_unconfigured",
            "JOBS_TOKEN is not configured",
            500,
        )
    header = request.headers.get("authorization", "")
    scheme, _, presented = header.partition(" ")
    if scheme.lower() != "bearer" or presented != expected:
        raise JobsError("unauthorized", "Invalid bearer token", 401)
