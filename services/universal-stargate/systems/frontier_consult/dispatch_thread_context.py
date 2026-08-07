"""Resolve team-dispatch prompt context from the caller-owned agent-bus thread."""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import urlencode

import httpx
from transport_utils import DEFAULT_AGENT_BUS_URL, make_async_client

from .admission import FrontierEndpointError

_PLACEHOLDER_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"<\s*INSERT[^>\n]*>", re.IGNORECASE),
    re.compile(r"<[^>\n]*HERE\s*>", re.IGNORECASE),
    re.compile(r"\{\{[^{}\n]+\}\}"),
)

# First-line markers of server-posted turns that must never become a model
# prompt. Single-thread Q/R reuse posts the generate pointer (and, on failure,
# a failure turn) onto the dispatch thread itself; a subsequent dispatch
# against the same thread would otherwise consume that server turn as the
# prompt verbatim (friction 23301 — threads 4741/4744 cascading pointer
# prompts). Markers mirror ``build_generate_dispatch_pointer``, the
# cursor-sdk packet pointer, and ``_post_api_role_dispatch_failure_turn``.
# Admit body first-line prefix (``cursor_sdk_coord_notify._admit_body``) — arc 6655.
_ADMIT_BODY_FIRST_LINE_MARKER = "Worker thread `"

_SERVER_TURN_MARKERS: tuple[str, ...] = (
    "generate dispatch — prompt on dispatch thread",
    "dispatch — see packet `",
    "generate dispatch failed (",
    _ADMIT_BODY_FIRST_LINE_MARKER,
)

PromptBindMode = Literal["frozen_turn", "latest", "explicit_inline", "sentinel_fallback"]


@dataclass(frozen=True, slots=True)
class GeneratePromptResolution:
    """Resolved generate prompt plus bind metadata for wide-(A) freeze."""

    text: str
    prompt_bind_mode: PromptBindMode
    prompt_turn_number: int | None = None


def is_server_dispatch_turn_body(text: str) -> bool:
    """True when *text* opens with a server-posted pointer/failure envelope."""
    stripped = text.strip()
    first_line = stripped.splitlines()[0] if stripped else ""
    return any(marker in first_line for marker in _SERVER_TURN_MARKERS)


def classify_bound_prompt_class(text: str) -> str:
    """Redaction-safe prompt feedstock class for spawn instrumentation (6655)."""
    stripped = text.strip()
    if not stripped:
        return "sentinel"
    first_line = stripped.splitlines()[0]
    if first_line.startswith(_ADMIT_BODY_FIRST_LINE_MARKER):
        return "admit_body"
    if is_server_dispatch_turn_body(stripped):
        if "generate dispatch — prompt on dispatch thread" in first_line:
            return "pointer_envelope"
        return "other_server"
    return "caller_prompt"


def bound_prompt_sha256_prefix(text: str, *, prefix_len: int = 12) -> str:
    """Short digest prefix for spawn bind instrumentation."""
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    first_line = text.strip().splitlines()[0] if text.strip() else ""
    line_head = first_line[:40]
    return f"{digest[:prefix_len]}:{line_head}"


def allowed_prompt_recipients(role: str) -> frozenset[str]:
    """Recipients that may address a thread-body generate prompt for *role*.

    Friction 24391: latching a self-note (``to=cursor``) or a prior role reply
    as the artisan prompt wasted a generate. ``dispatch`` is always allowed so
    callers can post briefs to the coord address; cursor-sdk also accepts the
    common IDE seat labels.
    """
    allowed = {role, "dispatch"}
    if role == "cursor-sdk":
        allowed.update({"cursor", "cursor-sdk", "claude-cursor"})
    return frozenset(allowed)


def _auth_headers() -> dict[str, str]:
    token = os.getenv("AGENT_BUS_TOKEN", "").strip()
    return {"Authorization": f"Bearer {token}"} if token else {}


def reject_unresolved_placeholders(
    *, request_id: str, text: str, field: str = "dispatch_thread_id"
) -> None:
    """Reject template-marker residue before a dispatched model can run."""
    for pattern in _PLACEHOLDER_PATTERNS:
        match = pattern.search(text)
        if match is not None:
            raise FrontierEndpointError(
                request_id=request_id,
                field=field,
                reason=(
                    "dispatch thread body contains unresolved template marker "
                    f"{match.group(0)!r}; replace placeholders before dispatch"
                ),
                status_code=422,
                code="dispatch_context_placeholder",
            )


def reject_non_prompt_latest_turn(
    *,
    request_id: str,
    dispatch_thread_id: str,
    role: str,
    turn: dict[str, Any],
) -> None:
    """Refuse latest turns that are not role-addressed caller prompts (a24391)."""
    from_raw = turn.get("from")
    to_raw = turn.get("to")
    from_agent = from_raw.strip() if isinstance(from_raw, str) else ""
    to_agent = to_raw.strip() if isinstance(to_raw, str) else ""
    allowed = allowed_prompt_recipients(role)
    if not to_agent or to_agent not in allowed or from_agent == role:
        raise FrontierEndpointError(
            request_id=request_id,
            field="dispatch_thread_id",
            reason=(
                f"latest turn on dispatch thread {dispatch_thread_id!r} is not a "
                f"prompt addressed to role {role!r} "
                f"(from={from_agent!r}, to={to_agent!r}; "
                f"allowed to∈{sorted(allowed)}). Post a fresh prompt turn "
                f"addressed to {role!r} (or to='dispatch') before dispatching, "
                "or pass prompt= / sidecar_ref= / packet_path= so brief and "
                "dispatch cannot desync"
            ),
            status_code=422,
            code="dispatch_thread_latest_not_prompt",
        )


def _validated_turn_body(
    *,
    request_id: str,
    dispatch_thread_id: str,
    role: str,
    turn: dict[str, Any],
    turn_label: str,
) -> str:
    """Shared consume gate for latest and frozen-turn reads."""
    body = turn.get("body")
    if not isinstance(body, str) or not body.strip():
        raise FrontierEndpointError(
            request_id=request_id,
            field="dispatch_thread_id",
            reason=f"{turn_label} on dispatch thread {dispatch_thread_id!r} has an empty body",
            status_code=422,
            code="dispatch_thread_empty_body",
        )
    text = body.strip()
    if is_server_dispatch_turn_body(text):
        raise FrontierEndpointError(
            request_id=request_id,
            field="dispatch_thread_id",
            reason=(
                f"{turn_label} on dispatch thread {dispatch_thread_id!r} is a "
                "server-posted dispatch pointer/failure/admit envelope, not a "
                "caller prompt. Post a fresh prompt turn on the thread before "
                "dispatching again, or pass split_thread=true to keep dispatch "
                "turns off the prompt thread"
            ),
            status_code=422,
            code="dispatch_thread_latest_is_pointer",
        )
    reject_non_prompt_latest_turn(
        request_id=request_id,
        dispatch_thread_id=dispatch_thread_id,
        role=role,
        turn=turn,
    )
    reject_unresolved_placeholders(request_id=request_id, text=text)
    return text


def _turn_number_from_payload(turn: dict[str, Any]) -> int | None:
    raw = turn.get("turn_number")
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


async def read_dispatch_thread_body_at_turn(
    *,
    request_id: str,
    dispatch_thread_id: str,
    role: str,
    turn_number: int,
) -> str:
    """Read a specific turn body from the caller-owned dispatch thread."""
    thread = dispatch_thread_id.strip()
    if not thread:
        raise FrontierEndpointError(
            request_id=request_id,
            field="dispatch_thread_id",
            reason="dispatch_thread_id is required",
            status_code=422,
        )
    role_key = role.strip()
    if not role_key:
        raise FrontierEndpointError(
            request_id=request_id,
            field="role",
            reason="role is required to validate the dispatch-thread prompt turn",
            status_code=422,
        )
    if turn_number < 1:
        raise FrontierEndpointError(
            request_id=request_id,
            field="dispatch_thread_id",
            reason=f"turn_number must be >= 1 (got {turn_number})",
            status_code=422,
            code="dispatch_thread_invalid_turn",
        )

    qs = urlencode({"thread": thread, "turn_number": turn_number})
    try:
        async with make_async_client(DEFAULT_AGENT_BUS_URL, timeout=10.0) as client:
            resp = await client.get(f"/turns/by-number?{qs}", headers=_auth_headers())
    except httpx.HTTPError as exc:
        raise FrontierEndpointError(
            request_id=request_id,
            field="dispatch_thread_id",
            reason=f"could not read dispatch thread {thread!r} turn {turn_number}: {exc}",
            status_code=503,
            code="dispatch_thread_read_failed",
        ) from exc

    if resp.status_code == 404:
        raise FrontierEndpointError(
            request_id=request_id,
            field="dispatch_thread_id",
            reason=(
                f"turn {turn_number} on dispatch thread {thread!r} was not found"
            ),
            status_code=422,
            code="dispatch_thread_turn_not_found",
        )
    if resp.status_code >= 400:
        raise FrontierEndpointError(
            request_id=request_id,
            field="dispatch_thread_id",
            reason=(
                f"agent-bus returned {resp.status_code} while reading dispatch "
                f"thread {thread!r} turn {turn_number}: {resp.text[:200]}"
            ),
            status_code=503,
            code="dispatch_thread_read_failed",
        )

    turn = resp.json()
    if not isinstance(turn, dict):
        raise FrontierEndpointError(
            request_id=request_id,
            field="dispatch_thread_id",
            reason=f"turn {turn_number} on dispatch thread {thread!r} is not an object",
            status_code=422,
            code="dispatch_thread_empty_body",
        )
    return _validated_turn_body(
        request_id=request_id,
        dispatch_thread_id=thread,
        role=role_key,
        turn=turn,
        turn_label=f"turn {turn_number}",
    )


async def read_latest_dispatch_thread_body(
    *, request_id: str, dispatch_thread_id: str, role: str
) -> str:
    """Read the latest turn body from the caller-owned dispatch thread."""
    resolution = await resolve_latest_dispatch_thread_turn(
        request_id=request_id,
        dispatch_thread_id=dispatch_thread_id,
        role=role,
    )
    return resolution.text


async def resolve_latest_dispatch_thread_turn(
    *, request_id: str, dispatch_thread_id: str, role: str
) -> GeneratePromptResolution:
    """Read latest turn body and capture its turn_number for wide-(A) freeze."""
    thread = dispatch_thread_id.strip()
    if not thread:
        raise FrontierEndpointError(
            request_id=request_id,
            field="dispatch_thread_id",
            reason="dispatch_thread_id is required",
            status_code=422,
        )
    role_key = role.strip()
    if not role_key:
        raise FrontierEndpointError(
            request_id=request_id,
            field="role",
            reason="role is required to validate the dispatch-thread prompt turn",
            status_code=422,
        )

    qs = urlencode({"thread": thread, "last": 1})
    try:
        async with make_async_client(DEFAULT_AGENT_BUS_URL, timeout=10.0) as client:
            resp = await client.get(f"/turns?{qs}", headers=_auth_headers())
    except httpx.HTTPError as exc:
        raise FrontierEndpointError(
            request_id=request_id,
            field="dispatch_thread_id",
            reason=f"could not read dispatch thread {thread!r}: {exc}",
            status_code=503,
            code="dispatch_thread_read_failed",
        ) from exc

    if resp.status_code == 404:
        raise FrontierEndpointError(
            request_id=request_id,
            field="dispatch_thread_id",
            reason=f"dispatch thread {thread!r} was not found",
            status_code=422,
            code="dispatch_thread_not_found",
        )
    if resp.status_code >= 400:
        raise FrontierEndpointError(
            request_id=request_id,
            field="dispatch_thread_id",
            reason=(
                f"agent-bus returned {resp.status_code} while reading dispatch "
                f"thread {thread!r}: {resp.text[:200]}"
            ),
            status_code=503,
            code="dispatch_thread_read_failed",
        )

    payload: dict[str, Any] = resp.json()
    turns = payload.get("turns")
    if not isinstance(turns, list) or not turns:
        raise FrontierEndpointError(
            request_id=request_id,
            field="dispatch_thread_id",
            reason=f"dispatch thread {thread!r} has no turns to dispatch",
            status_code=422,
            code="dispatch_thread_empty",
        )
    latest = turns[-1] if isinstance(turns[-1], dict) else None
    if latest is None:
        raise FrontierEndpointError(
            request_id=request_id,
            field="dispatch_thread_id",
            reason=f"latest turn on dispatch thread {thread!r} is not an object",
            status_code=422,
            code="dispatch_thread_empty_body",
        )
    text = _validated_turn_body(
        request_id=request_id,
        dispatch_thread_id=thread,
        role=role_key,
        turn=latest,
        turn_label="latest turn",
    )
    return GeneratePromptResolution(
        text=text,
        prompt_bind_mode="frozen_turn",
        prompt_turn_number=_turn_number_from_payload(latest),
    )


def _read_schemed_prompt_file(
    *,
    request_id: str,
    path_or_uri: str,
    field: str,
) -> str:
    from .handoff import _resolve_packet_file, _workspaces_root

    packet_file = _resolve_packet_file(_workspaces_root().resolve(), path_or_uri)
    if packet_file is None:
        raise FrontierEndpointError(
            request_id=request_id,
            field=field,
            reason=(
                f"{field} {path_or_uri!r} not found or unreadable under "
                "workspaces/cortex sandbox"
            ),
            status_code=422,
            code=f"{field}_unreadable",
        )
    return packet_file.read_text(encoding="utf-8", errors="replace")


def validate_explicit_prompt_sources(
    *,
    contract: str,
    packet_path: str | None,
    source_ref: str | None,
    prompt: str | None,
    sidecar_ref: str | None,
) -> None:
    """Reject explicit prompt sources that would be ignored or ambiguous."""
    inline_fields = [
        field
        for field, value in (("prompt", prompt), ("sidecar_ref", sidecar_ref))
        if value is not None
    ]
    if contract in ("implement", "wrap") and inline_fields:
        raise ValueError(
            f"{inline_fields[0]} is not supported with contract={contract!r}"
        )
    if source_ref is not None and inline_fields:
        raise ValueError("source_ref cannot be combined with prompt or sidecar_ref")
    explicit_fields = [
        field
        for field, value in (
            ("packet_path", packet_path),
            ("prompt", prompt),
            ("sidecar_ref", sidecar_ref),
        )
        if value is not None
    ]
    if len(explicit_fields) > 1:
        raise ValueError(
            "explicit prompt sources are mutually exclusive; pass exactly one "
            f"of packet_path, prompt, or sidecar_ref (received {explicit_fields})"
        )


async def resolve_generate_prompt_resolution(
    *,
    request_id: str,
    role: str,
    dispatch_thread_id: str | None,
    packet_path: str | None = None,
    prompt: str | None = None,
    sidecar_ref: str | None = None,
) -> GeneratePromptResolution:
    """Resolve generate prompt text with explicit-source precedence (SF1 / a24391).

    Exactly one of ``packet_path``, inline ``prompt``, or ``sidecar_ref`` may
    be explicit; otherwise the role-gated latest bus turn is the fallback.
    Thread fallback captures ``prompt_turn_number`` at read time for wide-(A).
    """
    explicit_fields = [
        field
        for field, value in (
            ("packet_path", packet_path),
            ("prompt", prompt),
            ("sidecar_ref", sidecar_ref),
        )
        if value is not None
    ]
    if len(explicit_fields) > 1:
        raise FrontierEndpointError(
            request_id=request_id,
            field=explicit_fields[1],
            reason=(
                "explicit prompt sources are mutually exclusive; pass exactly "
                "one of packet_path, prompt, or sidecar_ref"
            ),
            status_code=422,
            code="multiple_prompt_sources",
        )
    if packet_path is not None:
        text = _read_schemed_prompt_file(
            request_id=request_id,
            path_or_uri=packet_path,
            field="packet_path",
        ).strip()
        if not text:
            raise FrontierEndpointError(
                request_id=request_id,
                field="packet_path",
                reason=f"packet_path {packet_path!r} is empty",
                status_code=422,
                code="packet_path_empty",
            )
        reject_unresolved_placeholders(
            request_id=request_id, text=text, field="packet_path"
        )
        return GeneratePromptResolution(
            text=text, prompt_bind_mode="explicit_inline"
        )

    if prompt is not None:
        text = prompt.strip()
        if not text:
            raise FrontierEndpointError(
                request_id=request_id,
                field="prompt",
                reason="prompt must be non-empty when supplied",
                status_code=422,
                code="dispatch_prompt_empty",
            )
        reject_unresolved_placeholders(request_id=request_id, text=text, field="prompt")
        return GeneratePromptResolution(
            text=text, prompt_bind_mode="explicit_inline"
        )

    if sidecar_ref is not None:
        ref = sidecar_ref.strip()
        if not ref:
            raise FrontierEndpointError(
                request_id=request_id,
                field="sidecar_ref",
                reason="sidecar_ref must be non-empty when supplied",
                status_code=422,
                code="sidecar_ref_empty",
            )
        text = _read_schemed_prompt_file(
            request_id=request_id,
            path_or_uri=ref,
            field="sidecar_ref",
        ).strip()
        if not text:
            raise FrontierEndpointError(
                request_id=request_id,
                field="sidecar_ref",
                reason=f"sidecar_ref {ref!r} resolved to an empty file",
                status_code=422,
                code="sidecar_ref_empty",
            )
        reject_unresolved_placeholders(
            request_id=request_id, text=text, field="sidecar_ref"
        )
        return GeneratePromptResolution(
            text=text, prompt_bind_mode="explicit_inline"
        )

    if not dispatch_thread_id or not dispatch_thread_id.strip():
        raise FrontierEndpointError(
            request_id=request_id,
            field="dispatch_thread_id",
            reason=(
                "dispatch_thread_id is required when no packet_path, prompt, "
                "or sidecar_ref supplies the generate prompt"
            ),
            status_code=422,
        )
    return await resolve_latest_dispatch_thread_turn(
        request_id=request_id,
        dispatch_thread_id=dispatch_thread_id,
        role=role,
    )


async def resolve_generate_prompt_body(
    *,
    request_id: str,
    role: str,
    dispatch_thread_id: str | None,
    packet_path: str | None = None,
    prompt: str | None = None,
    sidecar_ref: str | None = None,
) -> str:
    """Resolve generate prompt text; see ``resolve_generate_prompt_resolution``."""
    resolution = await resolve_generate_prompt_resolution(
        request_id=request_id,
        role=role,
        dispatch_thread_id=dispatch_thread_id,
        packet_path=packet_path,
        prompt=prompt,
        sidecar_ref=sidecar_ref,
    )
    return resolution.text


def as_user_message(text: str) -> list[dict[str, str]]:
    """Internal pipeline request shape for a resolved dispatch-thread prompt."""
    return [{"role": "user", "content": text}]
