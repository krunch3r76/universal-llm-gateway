"""agent_bus ``request`` — retired cursor-auto entry (a:38728).

``_request_dispatch`` returns ``cursor_auto_retired_refusal`` and does not
write a turn. ``_resolve_hop_seat_request_refusal`` stays; hop imports it.
``resolve_request_id_intake`` lives in ``request_intake`` and hop imports it.
"""

from __future__ import annotations

from typing import Any

from mcp_events import record

_CURSOR_AUTO_RETIRED_ERROR = (
    "cursor-auto is retired (a:38728). Code commission: team_dispatch on ulg-code. "
    "Life CSE: life_dispatch. A bus turn: agent_bus send."
)


def cursor_auto_retired_refusal() -> dict[str, str]:
    """Teaching refusal for the retired cursor-auto commission path."""
    record("mcp.agentbus.request.rejected", reason="cursor_auto_retired")
    return {
        "error": _CURSOR_AUTO_RETIRED_ERROR,
        "reason": "cursor_auto_retired",
    }


def _resolve_hop_seat_request_refusal(
    *,
    thread_id: str | None,
    cse_registration_id: str | None,
    from_agent: str | None = None,
    audit: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Bind identity and refuse superseded predecessor writes when fenced.

    Refuses ``ambiguous_matches``; ``zero_matches`` / ``empty_snap`` only when
    the watch holds a ``registration_id`` (registration-less enroll stub admits on N=0).
    """
    from claude_bundles.request_admission_identity import gate_request_admission

    refusal = gate_request_admission(
        thread_id=thread_id,
        caller_registration_id=cse_registration_id,
        from_agent=from_agent,
        audit=audit,
    )
    if refusal is None:
        return None
    data = refusal.get("data") if isinstance(refusal.get("data"), dict) else {}
    record(
        "mcp.agentbus.request.rejected",
        reason=str(data.get("reason") or "hop_seat_refusal"),
        thread=thread_id,
        registration_id=cse_registration_id,
        code=str(refusal.get("code") or ""),
    )
    return refusal


def _request_dispatch(
    *,
    new_slug: str | None = None,
    thread: str | int | None = None,
    to: str = "cursor",
    subject: str = "",
    body: str = "",
    from_agent: str = "",
    tags: list[str] | None = None,
    sidecar_content: str | None = None,
    sidecar_slug: str | None = None,
    desired_model: str = "auto",
    desired_effort: str = "auto",
    contract: str = "answer",
    require_attended: bool = False,
    request_id: str | None = None,
    after_turn: int = 0,
    summary: str | None = None,
    cse_chat_url: str | None = None,
    cse_registration_id: str | None = None,
    escalation: str | None = None,
    lane: str | None = None,
    workspace: str | None = None,
    parent_thread: str | None = None,
    lane_role: str | None = None,
    prompt_uri: str | None = None,
    advisor_brief: str | None = None,
    work_key: str | None = None,
) -> dict[str, Any]:
    """Retired cursor-auto entry (a:38728). Refuses before any turn write."""
    return cursor_auto_retired_refusal()
