"""API dispatch path for grokbuild (mcp=False).

Direct LLM API call via Stargate — no grok CLI subprocess, no MCP tooling
available to the model inside the dispatch. Use when the prompt is
self-contained and the response is a text answer rather than a tool-driven task.

Caller contract:
  * ``system_context`` serves as the pre-staged corpus.
  * ``tier`` is canonical for ``reasoning.effort``. When ``model`` is
    supplied, api_dispatch still injects the tier preset iff
    ``MODEL_REGISTRY`` says the effective model supports reasoning effort;
    there is no caller-level ``reasoning_effort`` override.
  * Response envelope shape mirrors the CLI path so callers can treat both
    paths uniformly; fields absent from the API path are set to safe defaults.
  * Admission failures (``bad_tier``) return ``status="rejected"`` consistent
    with the CLI path; runtime failures (transport, HTTP error, parse error)
    return ``status="failed"``.
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

from grokbuild.constants import (
    _TIER_PRESETS,
    _VALID_TIERS,
    DEFAULT_TIMEOUT_SECONDS,
    DISPATCH_MODEL_ID,
    MODEL_REGISTRY,
    default_model_for_tier,
    envelope_metadata_model,
)
from grokbuild.envelope import _envelope_rejected
from grokbuild.events import (
    emit_grok_build_api_dispatch_called,
    emit_grok_build_api_dispatch_completed,
    emit_grok_build_api_dispatch_failed,
    emit_grok_build_api_dispatch_rejected,
)

logger = get_logger(__name__)


async def api_dispatch_op(
    cwd: str,
    prompt: str,
    *,
    system_context: str | None,
    model: str | None,
    session_id: str | None,
    tier: str = "thorough",
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

    if tier not in _VALID_TIERS:
        reason = f"tier must be one of {sorted(_VALID_TIERS)!r}, got {tier!r}"
        emit_grok_build_api_dispatch_rejected(
            dispatch_id=dispatch_id,
            reason_code="bad_tier",
            reason=reason,
            cwd=cwd,
            tier=tier,
            session_id=session_id or "",
        )
        return _envelope_rejected(
            dispatch_id, "read_only", cwd, session_id, model, "bad_tier", reason
        )

    effective_model = model if model is not None else default_model_for_tier(tier)
    if effective_model != DISPATCH_MODEL_ID:
        reason = (
            f"model must be {DISPATCH_MODEL_ID!r}, got {effective_model!r}; "
            "grokbuild api path does not admit Stargate pipeline models"
        )
        emit_grok_build_api_dispatch_rejected(
            dispatch_id=dispatch_id,
            reason_code="bad_model",
            reason=reason,
            cwd=cwd,
            tier=tier,
            session_id=session_id or "",
        )
        return _envelope_rejected(
            dispatch_id, "read_only", cwd, session_id, model, "bad_model", reason
        )

    metadata_model = envelope_metadata_model(model=model, tier=tier)

    # Inject reasoning.effort when the selected model supports it. api_dispatch
    # has no explicit reasoning_effort override, so tier remains canonical even
    # when the caller supplies a model.
    _caps = MODEL_REGISTRY.get(effective_model)
    _tier_reasoning_effort = _TIER_PRESETS[tier].reasoning_effort
    _inject_reasoning_effort = _caps is None or _caps.supports_reasoning_effort

    emit_grok_build_api_dispatch_called(
        dispatch_id=dispatch_id,
        cwd=cwd,
        model=metadata_model,
        effective_model=effective_model,
        tier=tier,
        session_id=session_id or "",
    )

    messages: list[dict[str, str]] = []
    if system_context:
        messages.append({"role": "system", "content": system_context})
    messages.append({"role": "user", "content": prompt})

    if timeout_seconds == 0:
        http_timeout: float | None = None
    elif timeout_seconds is not None:
        http_timeout = float(timeout_seconds)
    else:
        http_timeout = float(DEFAULT_TIMEOUT_SECONDS)

    request_body: dict[str, object] = {"model": effective_model, "messages": messages}
    if _inject_reasoning_effort:
        request_body["reasoning"] = {"effort": _tier_reasoning_effort}

    async with make_async_client(DEFAULT_STARGATE_URL, timeout=http_timeout) as client:
        try:
            resp = await client.post("/v1/chat/completions", json=request_body)
        except httpx.RequestError as exc:
            logger.error("api_dispatch transport failure: %s", exc)
            emit_grok_build_api_dispatch_failed(
                dispatch_id=dispatch_id,
                duration_s=time.monotonic() - t0,
                cwd=cwd,
                reason_code="api_unreachable",
                reason=str(exc),
                tier=tier,
                model=metadata_model,
                effective_model=effective_model,
                session_id=session_id or "",
            )
            return _envelope_failed(
                dispatch_id,
                t0,
                reason_code="api_unreachable",
                reason=str(exc),
                cwd=cwd,
                session_id=session_id,
                tier=tier,
                model=metadata_model,
            )

    duration_s = time.monotonic() - t0

    if resp.status_code >= 400:
        response_text = getattr(resp, "text", "")
        failure_reason = f"HTTP {resp.status_code}"
        if response_text:
            failure_reason = f"{failure_reason}: {response_text[:500]}"
        emit_grok_build_api_dispatch_failed(
            dispatch_id=dispatch_id,
            duration_s=duration_s,
            cwd=cwd,
            reason_code="api_error",
            reason=failure_reason,
            tier=tier,
            model=metadata_model,
            effective_model=effective_model,
            session_id=session_id or "",
        )
        return _envelope_failed(
            dispatch_id,
            t0,
            reason_code="api_error",
            reason=failure_reason,
            duration_s=duration_s,
            cwd=cwd,
            session_id=session_id,
            tier=tier,
            model=metadata_model,
        )

    try:
        data = resp.json()
        content: str = data["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError) as exc:
        logger.warning(
            "api_dispatch parse error dispatch_id=%s model=%s exc=%s",
            dispatch_id,
            effective_model,
            exc,
        )
        emit_grok_build_api_dispatch_failed(
            dispatch_id=dispatch_id,
            duration_s=duration_s,
            cwd=cwd,
            reason_code="invalid_response",
            reason=str(exc),
            tier=tier,
            model=metadata_model,
            effective_model=effective_model,
            session_id=session_id or "",
        )
        return _envelope_failed(
            dispatch_id,
            t0,
            reason_code="invalid_response",
            reason=str(exc),
            duration_s=duration_s,
            cwd=cwd,
            session_id=session_id,
            tier=tier,
            model=metadata_model,
        )

    logger.info(
        "api_dispatch completed dispatch_id=%s model=%s duration_s=%.2f",
        dispatch_id,
        effective_model,
        duration_s,
    )

    usage = data.get("usage") or {}
    emit_grok_build_api_dispatch_completed(
        dispatch_id=dispatch_id,
        duration_s=duration_s,
        cwd=cwd,
        model=metadata_model,
        effective_model=effective_model,
        tier=tier,
        session_id=session_id or "",
        prompt_tokens=int(usage.get("prompt_tokens", 0) or 0),
        completion_tokens=int(usage.get("completion_tokens", 0) or 0),
        total_tokens=int(usage.get("total_tokens", 0) or 0),
        reasoning_tokens=int(usage.get("reasoning_tokens", 0) or 0),
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
            "model": metadata_model,
            "tier": tier,
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
    cwd: str | None = None,
    session_id: str | None = None,
    tier: str | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "reason_code": reason_code,
        "reason": reason,
        "mcp": False,
    }
    if tier is not None:
        metadata["tier"] = tier
    if model is not None:
        metadata["model"] = model
    if session_id is not None:
        metadata["session_id"] = session_id
    if cwd is not None:
        metadata["cwd"] = cwd
    return {
        "dispatch_id": dispatch_id,
        "status": "failed",
        "stdout": "",
        "stderr": reason,
        "exit_code": None,
        "duration_s": duration_s if duration_s is not None else time.monotonic() - t0,
        "sidecar_path": None,
        "metadata": metadata,
    }
