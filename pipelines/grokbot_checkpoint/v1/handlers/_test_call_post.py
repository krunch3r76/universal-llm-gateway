"""Signature-aware run_post so master's handler stays callable."""

from __future__ import annotations

import inspect
from typing import Any

from .post import run_post


async def call_post(*args: Any, execution_id: str = "", **kwargs: Any) -> dict:
    """Pass execution_id only when this tree's run_post accepts it.

    Master's handler omits the parameter. Calling it unconditionally is a
    TypeError, which is not a behavioural failure.
    """
    if "execution_id" in inspect.signature(run_post).parameters:
        kwargs["execution_id"] = execution_id
    return await run_post(*args, **kwargs)
