"""Post-commit best-effort thread-480 debrief for session_close.

The debrief post runs outside the cortex DB transaction so a durable close
(transcript + journal + edge) never fails because agent-bus is unreachable.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Literal

from transport_utils import make_sync_client
from universal_logging import get_logger

from .session_close_enrichment_telemetry import emit_session_close_debrief_failed

logger = get_logger(__name__)

DEBRIEF_THREAD = "480"
DEBRIEF_TO = "all"
DEDUPE_SCAN_LAST = 50

DebriefStatus = Literal["posted", "skipped_existing", "failed", "disabled"]

_AGENT_BUS_URL = f"unix://{os.environ.get('AGENT_BUS_SOCK', '/tmp/universal-protocol/agent-bus.sock')}"


def session_debrief_token(session_id: str) -> str:
    return f"[session:{session_id}]"


@dataclass(frozen=True)
class DebriefOutcome:
    debrief_turn_number: int | None
    debrief_status: DebriefStatus
    debrief_body: str | None = None


def _agent_bus_token() -> str:
    return os.environ.get("AGENT_BUS_TOKEN", "").strip()


def _auth_headers() -> dict[str, str]:
    token = _agent_bus_token()
    return {"Authorization": f"Bearer {token}"} if token else {}


def compose_debrief_body(
    *,
    session_id: str,
    agent: str,
    summary: str,
    journal_row_id: int,
    transcript_depth: str,
    content_hash: str | None,
    domains: list[str] | None,
    decisions: list[str] | None,
    open_items: list[str] | None,
) -> str:
    """Format a thread-480 debrief body with the session dedupe token."""
    token = session_debrief_token(session_id)
    lines = [
        token,
        "",
        f"**Agent**: {agent}",
    ]
    if domains:
        lines.append(f"**Domains**: {', '.join(domains)}")
    lines.append(f"**Summary**: {summary}")
    if decisions:
        lines.append("**Decisions**:")
        lines.extend(f"- {item}" for item in decisions)
    if open_items:
        lines.append("**Open items**:")
        lines.extend(f"- {item}" for item in open_items)
    hash_fragment = (
        content_hash[:19] + "…"
        if content_hash and len(content_hash) > 19
        else content_hash
    )
    hash_part = f", content_hash={hash_fragment}" if hash_fragment else ""
    lines.append(
        f"**Transcript**: {session_id} (depth={transcript_depth}, "
        f"journal_row_id={journal_row_id}{hash_part})"
    )
    return "\n".join(lines)


def _fetch_recent_turns(*, session_id: str, agent: str) -> list[dict[str, Any]]:
    params = {
        "thread": DEBRIEF_THREAD,
        "last": DEDUPE_SCAN_LAST,
        "compact": "false",
    }
    with make_sync_client(_AGENT_BUS_URL, timeout=5.0) as client:
        resp = client.get("/turns", params=params, headers=_auth_headers())
        if resp.status_code != 200:
            logger.warning(
                "session_close debrief dedupe scan failed: HTTP %s", resp.status_code
            )
            emit_session_close_debrief_failed(
                session_id=session_id,
                agent=agent,
                stage="dedupe_scan",
                detail="GET /turns failed",
                status_code=resp.status_code,
            )
            return []
        data = resp.json()
        turns = data.get("turns", [])
        return turns if isinstance(turns, list) else []


def _find_existing_debrief(session_id: str, *, agent: str) -> int | None:
    token = session_debrief_token(session_id)
    for turn in _fetch_recent_turns(session_id=session_id, agent=agent):
        body = turn.get("body") or ""
        if token in body:
            turn_number = turn.get("turn_number")
            if isinstance(turn_number, int):
                return turn_number
    return None


def _post_debrief(
    *, from_agent: str, subject: str, body: str, session_id: str
) -> int | None:
    payload = {
        "thread": DEBRIEF_THREAD,
        "from": from_agent,
        "to": DEBRIEF_TO,
        "subject": subject,
        "body": body,
    }
    with make_sync_client(_AGENT_BUS_URL, timeout=5.0) as client:
        resp = client.post("/turns", json=payload, headers=_auth_headers())
        if resp.status_code not in (200, 201):
            logger.warning(
                "session_close debrief post failed: HTTP %s %s",
                resp.status_code,
                resp.text[:300],
            )
            emit_session_close_debrief_failed(
                session_id=session_id,
                agent=from_agent,
                stage="post",
                detail=resp.text[:300],
                status_code=resp.status_code,
            )
            return None
        data = resp.json()
        turn_number = data.get("turn_number")
        return int(turn_number) if turn_number is not None else None


def attempt_session_close_debrief(
    *,
    session_id: str,
    agent: str,
    summary: str,
    journal_row_id: int,
    transcript_depth: str,
    content_hash: str | None,
    domains: list[str] | None = None,
    decisions: list[str] | None = None,
    open_items: list[str] | None = None,
) -> DebriefOutcome:
    """Best-effort thread-480 debrief after a successful session close."""
    if not _agent_bus_token():
        logger.warning("session_close debrief skipped: AGENT_BUS_TOKEN not configured")
        return DebriefOutcome(None, "disabled")

    body = compose_debrief_body(
        session_id=session_id,
        agent=agent,
        summary=summary,
        journal_row_id=journal_row_id,
        transcript_depth=transcript_depth,
        content_hash=content_hash,
        domains=domains,
        decisions=decisions,
        open_items=open_items,
    )

    try:
        existing = _find_existing_debrief(session_id, agent=agent)
        if existing is not None:
            return DebriefOutcome(existing, "skipped_existing")

        subject = f"Session close: {session_id}"
        turn_number = _post_debrief(
            from_agent=agent,
            subject=subject,
            body=body,
            session_id=session_id,
        )
        if turn_number is None:
            emit_session_close_debrief_failed(
                session_id=session_id,
                agent=agent,
                stage="post",
                detail="turn_number missing in agent-bus response",
            )
            return DebriefOutcome(None, "failed", body)
        logger.info(
            "session_close debrief posted: session=%s thread=%s turn=%d",
            session_id,
            DEBRIEF_THREAD,
            turn_number,
        )
        return DebriefOutcome(turn_number, "posted")
    except Exception as exc:
        logger.warning(
            "session_close debrief failed for %s",
            session_id,
            exc_info=True,
        )
        emit_session_close_debrief_failed(
            session_id=session_id,
            agent=agent,
            stage="unhandled",
            detail=type(exc).__name__,
        )
        return DebriefOutcome(None, "failed", body)


def debrief_outcome_as_dict(outcome: DebriefOutcome) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "debrief_turn_number": outcome.debrief_turn_number,
        "debrief_status": outcome.debrief_status,
    }
    if outcome.debrief_body is not None:
        payload["debrief_body"] = outcome.debrief_body
    return payload
