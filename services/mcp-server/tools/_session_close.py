"""Session close helper — protocol template and pre-fetched data for reliable closes.

The session_close tool is a structured reminder: it mints a transcript ID,
fetches data the agent needs (bus turn number), and returns the exact protocol
steps. The agent executes each step — the tool does not perform the close itself.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote, urlencode

from ._cortex_relay import _cx
from .local_api import _relay


def build_session_close(
    agent: str,
    session_id: str = "",
) -> dict[str, Any]:
    """Build session close instructions with pre-fetched data.

    Returns a structured response containing transcript ID, file paths,
    bus thread state, and step-by-step protocol instructions.
    """
    now = datetime.now(UTC)
    if not session_id:
        session_id = f"{agent}-{now.strftime('%Y-%m-%d-%H%M')}"

    transcript_id = session_id

    entity_key = f"transcript:{transcript_id}"
    existing = _cx("GET", f"/entities/{quote(entity_key, safe=':')}")
    if "error" not in existing:
        return {
            "error": "transcript_exists",
            "transcript_id": transcript_id,
            "detail": (
                f"Entity {entity_key} already exists. This session may have "
                "already been closed, or the transcript ID collides with an "
                "earlier session in the same minute. Append a suffix or use "
                "the next minute."
            ),
        }

    bus_last_turn: int | None = None
    try:
        threads_raw = _relay("agent-bus", "GET", "/threads?status=active")
        threads = (
            threads_raw.get("threads", []) if isinstance(threads_raw, dict) else []
        )
        journal_thread_id: str | None = None
        for t in threads:
            if t.get("slug") == "agent-activity-journal":
                journal_thread_id = str(t.get("id", ""))
                break
        if journal_thread_id:
            turns_qs = urlencode(
                {"thread": journal_thread_id, "last": 1, "compact": "true"}
            )
            turns_raw = _relay("agent-bus", "GET", f"/turns?{turns_qs}")
            turns = turns_raw.get("turns", []) if isinstance(turns_raw, dict) else []
            if turns:
                bus_last_turn = turns[0].get("turn_number")
    except Exception:
        pass

    transcript_path = f"notes/system/transcripts/{transcript_id}.md"
    journal_path = f"notes/system/journal/{transcript_id}.md"

    steps = _build_steps(
        agent=agent,
        transcript_id=transcript_id,
        transcript_path=transcript_path,
        journal_path=journal_path,
        bus_last_turn=bus_last_turn,
    )

    return {
        "transcript_id": transcript_id,
        "transcript_path": transcript_path,
        "journal_path": journal_path,
        "bus_thread_480_last_turn": bus_last_turn,
        "steps": steps,
    }


def _build_steps(
    *,
    agent: str,
    transcript_id: str,
    transcript_path: str,
    journal_path: str,
    bus_last_turn: int | None,
) -> list[dict[str, str]]:
    """Build the ordered list of session close protocol steps."""
    after_turn = bus_last_turn if bus_last_turn is not None else "UNKNOWN"

    return [
        {
            "step": "1",
            "action": "Write transcript markdown",
            "detail": (
                f"Write turn-by-turn transcript to: "
                f"`fs(sandbox='files', op='write', path='{transcript_path}', content='...')`\n"
                "Include: H1 title, H2 per exchange (### User / ### Claude verbatim), "
                "## Session Summary at end with Decisions, Files modified, Open items, "
                "Attachments, Journal, Transcript fields."
            ),
        },
        {
            "step": "2",
            "action": "Seed outstanding assertions",
            "detail": (
                "Seed 2-5 self-observations about session effectiveness: "
                f'`cortex(tool=\'observe\', arguments=\'{{"entity_id": "ai_agent:{agent}-claude", '
                f'"claim": "...", "agent": "{agent}"}}\')`\n'
                "Cover: context gaps, workflow friction, corrections received, patterns noticed."
            ),
        },
        {
            "step": "3",
            "action": "Write journal row",
            "detail": (
                f'`cortex(tool=\'journal_write\', arguments=\'{{"agent": "{agent}", '
                f'"session_id": "{transcript_id}", '
                '"summary": "...", "domains": ["..."], '
                '"decisions": ["..."], "open_items": ["..."]}}\')`'
            ),
        },
        {
            "step": "4",
            "action": "Post bus debrief (thread 480)",
            "detail": (
                f'`agent_bus(tool=\'reply\', arguments=\'{{"thread": "480", "to": "all", '
                f'"subject": "...", "body": "**Agent**: {agent}\\n'
                "**Domains**: ...\\n**Changes**: ...\\n**Infrastructure**: ...\\n"
                f"**Open items**: ...\\n**Transcript**: {transcript_id}\\n\\n"
                f'Signed, (Cursor) Claude", "after_turn": {after_turn}}}\')`'
            ),
        },
        {
            "step": "5",
            "action": "Markdown audit",
            "detail": (
                "Review docs touched this session. For each: was it updated to "
                "reflect decisions? Does it accurately reflect current state? "
                "Surface gaps before closing."
            ),
        },
        {
            "step": "6",
            "action": "Report transcript ID to user",
            "detail": f"End with: Session closed — `transcript:{transcript_id}`",
        },
    ]
