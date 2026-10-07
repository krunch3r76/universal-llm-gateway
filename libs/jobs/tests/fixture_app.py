"""Test-only ASGI factory for the restart subprocess.

``python -m jobs.tests.fixture_app`` serves one ticker job on ``JOBS_SOCK``.
The production registry is not loaded. The parent reads ``JOBS_EVENT_SINK``
for ``jobs.run.lost`` after it kills this process.
"""

from __future__ import annotations

import os
from datetime import date

import uvicorn
from pydantic import BaseModel, ConfigDict

from jobs.server import create_app
from jobs.spec import GraduationTarget, JobSpec, load_registry


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _argv(_args: BaseModel) -> list[str]:
    return []


def build_registry() -> tuple[JobSpec, ...]:
    """Return a one-job registry whose executable is the ticker fixture."""
    spec = JobSpec(
        name="ticker",
        description="Test job that prints once a second until killed.",
        surfaces=frozenset({"code"}),
        args_model=_Args,
        argv=_argv,
        executable="libs/jobs/tests/fixtures/ticker.py",
        idle_seconds=120,
        graduates_to=GraduationTarget(
            kind="manage_lifecycle",
            owner="watch",
            note="Test fixture; not a production graduation target.",
        ),
        sunset=date(2027, 4, 1),
        handle="capability:jobs/ticker",
    )
    return load_registry((spec,))


def main() -> None:
    """Serve the fixture app on the UDS path in JOBS_SOCK."""
    application = create_app(build_registry())
    uvicorn.run(application, uds=os.environ["JOBS_SOCK"], log_level="warning")


if __name__ == "__main__":
    main()
