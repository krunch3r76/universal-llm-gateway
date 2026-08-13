"""Spies for CDP commission wire-forwarding boundaries.

Absorbing ``**_kwargs`` doubles cannot catch a dropped ``reasoning_effort``.
These helpers require the keyword so an omission fails the suite.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock


def commission_spy(
    *,
    execution_id: str = "exec-spy",
    capture: list[dict[str, Any]] | None = None,
) -> AsyncMock:
    """Return an AsyncMock that fails if ``reasoning_effort`` is not keyworded.

    Callers that intentionally omit effort must pass ``reasoning_effort=None``.
    """
    calls = capture if capture is not None else []

    async def _impl(
        job: Any,
        *,
        model: str,
        reasoning_effort: str | None = ...,  # type: ignore[assignment]
        purpose: str | None = None,
        mission_kind: str | None = None,
        parent_thread: str | None = None,
        stargate_url: str | None = None,
        prompt_override: str | None = None,
    ) -> dict[str, Any]:
        if reasoning_effort is ...:
            raise AssertionError(
                "commission_cdp_escalation called without reasoning_effort= "
                "(pass None explicitly when unpinned)"
            )
        job_body = getattr(job, "body", None)
        calls.append(
            {
                "model": model,
                "reasoning_effort": reasoning_effort,
                "purpose": purpose,
                "mission_kind": mission_kind,
                "parent_thread": parent_thread,
                "body": job_body,
                "prompt_override": prompt_override,
                # What Stargate actually receives as the prompt.
                "prompt": prompt_override or job_body,
            }
        )
        return {"ok": True, "execution_id": execution_id, "status_code": 202}

    mock = AsyncMock(side_effect=_impl)
    mock.calls = calls  # type: ignore[attr-defined]
    return mock
