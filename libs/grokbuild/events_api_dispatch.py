"""Event factories and emitters for grokbuild api-dispatch lifecycle (mcp=False).

Distinct namespace (``mcp.grokbuild.apidispatch.*``) from the CLI dispatch
lifecycle (``mcp.grokbuild.dispatch.*``) so downstream consumers can
discriminate path-by-signal without inspecting payload fields. The api
path is a direct Stargate /v1/chat/completions call with no subprocess,
no MCP tooling, and no git-audit surface — so the payload shape is
narrower than the CLI dispatch events (no git_status_pre/post, no
read_only_violation, no exit_code, no truncation).

Signal segments use compound nouns ("apidispatch") to satisfy the
``^[a-z]+(\\.[a-z]+){1,4}$`` signal-format invariant — same convention
as ``toolcalls`` / ``zerotoolcalls`` in events_dispatch.

Completed-event payload includes ``prompt_tokens`` / ``completion_tokens``
/ ``total_tokens`` / ``reasoning_tokens`` parsed from the xAI / OpenAI-
compatible ``usage`` block. Reasoning-tokens defaults to 0 when the
backing model does not surface that field (non-reasoning variants).
"""

from __future__ import annotations

from universal_event_bus import Event, event_factory

from grokbuild.events_core import _emit


@event_factory
def GrokBuildApiDispatchCalled(  # noqa: N802
    dispatch_id: str,
    cwd: str,
    model: str,
    tier: str,
    session_id: str = "",
    effective_model: str = "",
) -> Event:
    return Event(
        signal="mcp.grokbuild.apidispatch.called",
        payload={
            "dispatch_id": dispatch_id,
            "cwd": cwd,
            "model": model,
            "effective_model": effective_model,
            "tier": tier,
            "session_id": session_id,
        },
        scope="global",
    )


@event_factory
def GrokBuildApiDispatchCompleted(  # noqa: N802
    dispatch_id: str,
    duration_s: float,
    cwd: str,
    model: str,
    tier: str,
    session_id: str = "",
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    total_tokens: int = 0,
    reasoning_tokens: int = 0,
    effective_model: str = "",
) -> Event:
    return Event(
        signal="mcp.grokbuild.apidispatch.completed",
        payload={
            "dispatch_id": dispatch_id,
            "duration_s": duration_s,
            "cwd": cwd,
            "model": model,
            "effective_model": effective_model,
            "tier": tier,
            "session_id": session_id,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
            "reasoning_tokens": reasoning_tokens,
        },
        scope="global",
    )


@event_factory
def GrokBuildApiDispatchFailed(  # noqa: N802
    dispatch_id: str,
    duration_s: float,
    cwd: str,
    reason_code: str,
    reason: str,
    tier: str,
    model: str = "",
    session_id: str = "",
    effective_model: str = "",
) -> Event:
    return Event(
        signal="mcp.grokbuild.apidispatch.failed",
        payload={
            "dispatch_id": dispatch_id,
            "duration_s": duration_s,
            "cwd": cwd,
            "reason_code": reason_code,
            "reason": reason,
            "tier": tier,
            "model": model,
            "effective_model": effective_model,
            "session_id": session_id,
        },
        scope="global",
    )


@event_factory
def GrokBuildApiDispatchRejected(  # noqa: N802
    dispatch_id: str,
    reason_code: str,
    reason: str,
    cwd: str,
    tier: str,
    session_id: str = "",
) -> Event:
    return Event(
        signal="mcp.grokbuild.apidispatch.rejected",
        payload={
            "dispatch_id": dispatch_id,
            "reason_code": reason_code,
            "reason": reason,
            "cwd": cwd,
            "tier": tier,
            "session_id": session_id,
        },
        scope="global",
    )


# Typed wrappers — keyword-only, matching factory signatures (review G4).


def emit_grok_build_api_dispatch_called(
    *,
    dispatch_id: str,
    cwd: str,
    model: str,
    tier: str,
    session_id: str = "",
    effective_model: str = "",
) -> None:
    _emit(
        GrokBuildApiDispatchCalled(
            dispatch_id=dispatch_id,
            cwd=cwd,
            model=model,
            tier=tier,
            session_id=session_id,
            effective_model=effective_model,
        )
    )


def emit_grok_build_api_dispatch_completed(
    *,
    dispatch_id: str,
    duration_s: float,
    cwd: str,
    model: str,
    tier: str,
    session_id: str = "",
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    total_tokens: int = 0,
    reasoning_tokens: int = 0,
    effective_model: str = "",
) -> None:
    _emit(
        GrokBuildApiDispatchCompleted(
            dispatch_id=dispatch_id,
            duration_s=duration_s,
            cwd=cwd,
            model=model,
            tier=tier,
            session_id=session_id,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            reasoning_tokens=reasoning_tokens,
            effective_model=effective_model,
        )
    )


def emit_grok_build_api_dispatch_failed(
    *,
    dispatch_id: str,
    duration_s: float,
    cwd: str,
    reason_code: str,
    reason: str,
    tier: str,
    model: str = "",
    session_id: str = "",
    effective_model: str = "",
) -> None:
    _emit(
        GrokBuildApiDispatchFailed(
            dispatch_id=dispatch_id,
            duration_s=duration_s,
            cwd=cwd,
            reason_code=reason_code,
            reason=reason,
            tier=tier,
            model=model,
            session_id=session_id,
            effective_model=effective_model,
        )
    )


def emit_grok_build_api_dispatch_rejected(
    *,
    dispatch_id: str,
    reason_code: str,
    reason: str,
    cwd: str,
    tier: str,
    session_id: str = "",
) -> None:
    _emit(
        GrokBuildApiDispatchRejected(
            dispatch_id=dispatch_id,
            reason_code=reason_code,
            reason=reason,
            cwd=cwd,
            tier=tier,
            session_id=session_id,
        )
    )
