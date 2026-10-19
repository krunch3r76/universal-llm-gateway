"""Agent-bus notices for manage forced-restart gate outcomes (G1 busy + force)."""

from __future__ import annotations

import asyncio
import subprocess
import sys
from pathlib import Path
from typing import Any

from universal_logging import get_logger

logger = get_logger(__name__)

_NOTICE_BODY_MAX = 1800
_FROM_AGENT = "manage"


def resolve_notice_thread_id(
    *,
    caller_thread_id: str | None,
    caller_dispatch_id: str | None,
    holder: dict[str, Any] | None,
) -> str | None:
    """Pick the dispatching lane thread for a force-gate notice."""
    for candidate in (
        caller_thread_id,
        _thread_from_dispatch(caller_dispatch_id),
        (holder or {}).get("thread_id"),
    ):
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    return None


def _thread_from_dispatch(dispatch_id: str | None) -> str | None:
    ident = (dispatch_id or "").strip()
    if not ident:
        return None
    try:
        from services.git_integration_worker.cursor_dispatch_ledger import (
            CursorDispatchLedger,
        )

        with CursorDispatchLedger.instance()._connect() as conn:
            row = conn.execute(
                "SELECT thread_id FROM cursor_sdk_dispatches WHERE dispatch_id=?",
                (ident,),
            ).fetchone()
        if row is None:
            return None
        value = row["thread_id"]
        return str(value).strip() if value else None
    except Exception:
        logger.debug(
            "force-gate notice: ledger thread lookup failed for %s",
            ident,
            exc_info=True,
        )
        return None


def _agent_bus_script() -> Path:
    return Path(__file__).resolve().parents[3] / "agent-bus"


def _truncate_body(text: str) -> str:
    if len(text) <= _NOTICE_BODY_MAX:
        return text
    return text[: _NOTICE_BODY_MAX - 3] + "..."


def build_force_gate_notice_body(
    *,
    outcome: str,
    service: str,
    action: str,
    force_reason: str | None,
    busy_summary: str,
    holder: dict[str, Any] | None,
) -> str:
    lines = [
        f"manage force-gate {outcome}",
        f"service={service} action={action}",
    ]
    if force_reason and force_reason.strip():
        lines.append(f"force_reason={force_reason.strip()}")
    if busy_summary:
        lines.append(f"busy_summary={busy_summary}")
    if holder:
        dispatch_id = holder.get("dispatch_id")
        thread_id = holder.get("thread_id")
        if dispatch_id:
            lines.append(f"holder_dispatch_id={dispatch_id}")
        if thread_id:
            lines.append(f"holder_thread_id={thread_id}")
    return _truncate_body("\n".join(lines))


def schedule_force_gate_notice(
    *,
    thread_id: str | None,
    subject: str,
    body: str,
) -> None:
    if not thread_id:
        logger.warning(
            "force-gate notice skipped: no dispatching lane thread (subject=%s)",
            subject[:120],
        )
        return

    async def _post() -> None:
        await asyncio.to_thread(
            _post_notice_sync,
            thread_id=thread_id,
            subject=subject,
            body=body,
        )

    asyncio.create_task(_post())


def _post_notice_sync(*, thread_id: str, subject: str, body: str) -> None:
    script = _agent_bus_script()
    proc = subprocess.run(
        [
            sys.executable,
            str(script),
            "reply",
            "--thread",
            thread_id,
            "--to",
            "cursor",
            "--from-agent",
            _FROM_AGENT,
            "--subject",
            subject,
            "--body",
            body,
            "--after-turn",
            "0",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        logger.warning(
            "force-gate notice post failed thread=%s exit=%s detail=%s",
            thread_id,
            proc.returncode,
            detail[:400],
        )
