"""API dispatch path for grokbuild (mcp=False).

Direct LLM API call via Stargate — no grok CLI subprocess, no MCP tooling
available to the model inside the dispatch. Use when the prompt is
self-contained and the response is a text answer rather than a tool-driven task.

Caller contract:
  * ``system_context`` serves as the pre-staged corpus.
  * Response envelope shape mirrors the CLI path so callers can treat both
    paths uniformly; fields absent from the API path are set to safe defaults.
  * No git audit fields (``git_status_pre/post``, ``git_diff_stat``) — this
    path never touches the worktree filesystem.
  * ``sidecar_path`` is always None.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

import httpx
from transport_utils import DEFAULT_STARGATE_URL, make_async_client
from universal_logging import get_logger

logger = get_logger(__name__)

_DEFAULT_MODEL = "xai/grok-4.3__effort_medium"
_DEFAULT_TIMEOUT = 120.0


async def api_dispatch_op(
    cwd: str,
    prompt: str,
    *,
    system_context: str | None,
    model: str | None,
    session_id: str | None,
    dispatch_id: str | None = None,
    timeout_seconds: int | None = None,
) -> dict[str, Any]:
    """Call the LLM API directly and return a grokbuild-compatible envelope.

    No subprocess is spawned. The model receives ``system_context`` +
    ``prompt`` as a two-turn conversation and its text response becomes
    ``stdout`` in the returned envelope.
    """
    if dispatch_id is None:
        dispatch_id = str(uuid.uuid4())

    t0 = time.monotonic()
    messages: list[dict[str, str]] = []
    if system_context:
        messages.append({"role": "system", "content": system_context})
    messages.append({"role": "user", "content": prompt})

    timeout = float(timeout_seconds) if timeout_seconds else _DEFAULT_TIMEOUT

    async with make_async_client(DEFAULT_STARGATE_URL, timeout=timeout) as client:
        try:
            resp = await client.post(
                "/v1/chat/completions",
                json={"model": model or _DEFAULT_MODEL, "messages": messages},
            )
        except httpx.RequestError as exc:
            logger.error("api_dispatch transport failure: %s", exc)
            return _envelope_failed(
                dispatch_id, t0, reason_code="api_unreachable", reason=str(exc)
            )

    duration_s = time.monotonic() - t0

    if resp.status_code >= 400:
        return _envelope_failed(
            dispatch_id,
            t0,
            reason_code="api_error",
            reason=f"HTTP {resp.status_code}",
            duration_s=duration_s,
        )

    try:
        data = resp.json()
        content: str = data["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError) as exc:
        return _envelope_failed(
            dispatch_id,
            t0,
            reason_code="invalid_response",
            reason=str(exc),
            duration_s=duration_s,
        )

    logger.info(
        "api_dispatch completed dispatch_id=%s model=%s duration_s=%.2f",
        dispatch_id,
        model or _DEFAULT_MODEL,
        duration_s,
    )

    return {
        "dispatch_id": dispatch_id,
        "status": "completed",
        "stdout": content,
        "stderr": "",
        "exit_code": 0,
        "duration_s": duration_s,
        "sidecar_path": None,
        "metadata": {
            "reason_code": "",
            "reason": "",
            "mcp": False,
            "model": model or _DEFAULT_MODEL,
            "session_id": session_id,
            "mode": "read_only",
            "cwd": cwd,
        },
    }


def _envelope_failed(
    dispatch_id: str,
    t0: float,
    *,
    reason_code: str,
    reason: str,
    duration_s: float | None = None,
) -> dict[str, Any]:
    return {
        "dispatch_id": dispatch_id,
        "status": "failed",
        "stdout": "",
        "stderr": reason,
        "exit_code": None,
        "duration_s": duration_s if duration_s is not None else time.monotonic() - t0,
        "sidecar_path": None,
        "metadata": {"reason_code": reason_code, "reason": reason, "mcp": False},
    }
