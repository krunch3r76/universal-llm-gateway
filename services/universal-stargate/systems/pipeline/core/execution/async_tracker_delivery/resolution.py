"""``to_agent`` resolution + UTC ISO timestamp helper.

These helpers are factored out of the on-behalf delivery path so that
``_resolve_to_agent`` is independently testable and ``_utc_now_iso`` can
be shared by any future submodule that needs a normalized timestamp on
``record.thread_reply_observed_at`` writes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..async_tracker import PipelineExecutionRecord


def _resolve_to_agent(
    record: PipelineExecutionRecord, *, last_turn_from: str | None
) -> str | None:
    """Pick ``to_agent`` for the on-behalf reply turn.

    Order: ``caller_agent`` (the originating dispatcher) when distinct from
    ``from_agent``; otherwise the thread's last turn author; otherwise None
    (caller must resolve — delivery fails with ``unresolved_to_agent``).
    """
    from_agent = record.from_agent
    if record.caller_agent and record.caller_agent != from_agent:
        return record.caller_agent
    if last_turn_from and last_turn_from != from_agent:
        return last_turn_from
    return None


def _utc_now_iso() -> str:
    """Return current UTC instant as ISO-8601 with ``Z`` suffix.

    Used to stamp ``record.thread_reply_observed_at`` on successful 2xx
    on-behalf POSTs. Matches the agent-bus timestamp format so downstream
    consumers can compare without re-parsing.
    """
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat().replace("+00:00", "Z")
