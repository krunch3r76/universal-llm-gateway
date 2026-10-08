"""Fetch-family agent_bus dispatchers: fetch, fetch_unread, get."""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlencode

from agent_bus_store.recipients import turn_mark_read_eligible
from mcp_events import record

from .._agent_bus_author import resolve_dispatch_from_agent
from ._shared import _FETCH_CONTEXT_CAP, _structured_relay_error, relay

logger = logging.getLogger(__name__)


def _merge_advisories(result: dict[str, Any], advisories: list[str]) -> dict[str, Any]:
    """Attach mark-read advisories without dropping a relay payload."""
    if not advisories or not isinstance(result, dict) or "error" in result:
        return result
    merged = "; ".join(advisories)
    prior = result.get("argument_rewrite_advisory")
    result["argument_rewrite_advisory"] = f"{prior}; {merged}" if prior else merged
    return result


def _omitted_author_advisory(op: str, reader: str) -> str:
    return (
        f"{op}: mark_read identity is {reader!r} from "
        "resolve_dispatch_from_agent() (from_agent omitted)"
    )


def _seat_mismatch_advisory(op: str, to_seat: str, reader: str) -> str:
    return (
        f"{op}: mark_read refused — to={to_seat!r} does not match caller seat "
        f"{reader!r} from resolve_dispatch_from_agent()"
    )


def _resolve_mark_reader(
    *,
    op: str,
    from_agent: str,
    to_seat: str | None,
) -> tuple[str | None, list[str], dict[str, Any] | None, bool]:
    """Resolve who mark_read may stamp.

    Returns ``(seat, advisories, error, apply_mark)``. A ``to`` filter that is
    not the resolved caller refuses the stamp and names both seats. Omitting
    ``from_agent`` still marks, and the advisory names the resolved identity.
    """
    supplied = bool(from_agent.strip())
    reader, author_err = resolve_dispatch_from_agent(from_agent)
    if author_err is not None:
        return None, [], author_err, False
    advisories: list[str] = []
    if not reader:
        advisories.append(
            f"{op}: mark_read skipped — reader seat could not be resolved"
        )
        return None, advisories, None, False
    if not supplied:
        advisories.append(_omitted_author_advisory(op, reader))
    if to_seat and to_seat != reader:
        advisories.append(_seat_mismatch_advisory(op, to_seat, reader))
        return reader, advisories, None, False
    return reader, advisories, None, True


def _fetch_impl(
    *,
    to: str | None,
    thread: str | None,
    last: int | None,
    unread: bool,
    mark_read: bool,
    compact: bool,
    after_turn: int | None = None,
    mark_read_seat: str | None = None,
) -> dict[str, Any]:
    if to is None and thread is None:
        return {"error": "fetch requires at least one of: to, thread"}

    params: dict[str, Any] = {}
    if thread is not None:
        params["thread"] = thread
    if to is not None:
        params["to"] = to
    if unread:
        params["unread"] = "true"
    if compact:
        params["compact"] = "true"
    if last is not None:
        params["last"] = last
    if after_turn is not None:
        params["after_turn"] = after_turn
    if mark_read:
        params["mark_read"] = "true"
    if mark_read_seat:
        params["mark_read_seat"] = mark_read_seat

    qs = urlencode(params)
    result = relay("agent-bus", "GET", f"/turns?{qs}")

    if "error" in result:
        structured = _structured_relay_error(result, op="fetch")
        if structured is not None:
            return structured
        return {"error": f"agent-bus error: {result['error']}"}

    turns: list[Any] = result if isinstance(result, list) else result.get("turns", [])
    count = len(turns)
    logger.info(
        "agent_bus fetch: to=%s thread=%s mark_read=%s -> %d turns",
        to,
        thread,
        mark_read,
        count,
    )
    record(
        "mcp.agentbus.turns.fetched",
        to=to or "",
        thread=thread or "",
        count=count,
        mark_read=mark_read,
    )
    return result


def _fetch_unread_toc_impl(
    *,
    to: str,
    mark_read: bool,
    active_since: str | None = None,
    limit: int | None = None,
    all_threads: bool = False,
) -> dict[str, Any]:
    """Recipient-scoped unread inbox digest via GET /turns/unread-toc."""
    params: dict[str, Any] = {"to": to}
    if mark_read:
        params["mark_read"] = "true"
    if active_since is not None:
        params["active_since"] = active_since
    if limit is not None:
        params["limit"] = limit
    if all_threads:
        params["all"] = "true"
    qs = urlencode(params)
    result = relay("agent-bus", "GET", f"/turns/unread-toc?{qs}")

    if isinstance(result, dict) and "error" in result:
        structured = _structured_relay_error(result, op="fetch_unread")
        if structured is not None:
            return structured
        return {"error": f"agent-bus error: {result['error']}"}

    thread_count = len(result.get("threads", [])) if isinstance(result, dict) else 0
    logger.info(
        "agent_bus fetch_unread (toc): to=%s mark_read=%s -> %d threads",
        to,
        mark_read,
        thread_count,
    )
    record(
        "mcp.agentbus.unread_toc.fetched",
        to=to,
        thread_count=thread_count,
        mark_read=mark_read,
    )
    return result


def _get_impl(
    *,
    thread: str,
    turn_number: int | str,
    mark_read: bool = False,
    reader_agent: str = "",
) -> dict[str, Any]:
    """Direct single-turn lookup via GET /turns/by-number."""
    qs = urlencode({"thread": thread, "turn_number": turn_number})
    result = relay("agent-bus", "GET", f"/turns/by-number?{qs}")
    if isinstance(result, dict) and "error" in result:
        structured = _structured_relay_error(result, op="get")
        if structured is not None:
            return structured
        return {"error": f"agent-bus error: {result['error']}"}
    advisories: list[str] = []
    if mark_read and isinstance(result, dict):
        to_agent = str(result.get("to_agent") or "")
        if reader_agent and turn_mark_read_eligible(
            seat=reader_agent, to_agent=to_agent
        ):
            if result.get("read_at") is None:
                turn_id = result.get("id")
                if turn_id is not None:
                    patched = relay("agent-bus", "PATCH", f"/turns/{turn_id}/read")
                    if isinstance(patched, dict) and patched.get("read_at"):
                        result = dict(result)
                        result["read_at"] = patched["read_at"]
        elif mark_read:
            advisories.append(
                "get: mark_read skipped — turn is not addressed to the caller "
                f"(to={to_agent!r}, reader={reader_agent!r})"
            )
    record(
        "mcp.agentbus.turn.detail.fetched",
        thread=thread,
        turn_number=str(turn_number),
        mark_read=mark_read,
    )
    out: dict[str, Any] = {"turn": result}
    if advisories:
        out["argument_rewrite_advisory"] = "; ".join(advisories)
    return out


def _fetch_unread_dispatch(
    *,
    to: str | None = None,
    thread: str | int | None = None,
    mark_read: bool = False,
    compact: bool = False,
    active_since: str | None = None,
    limit: int | None = None,
    all_threads: bool = False,
    from_agent: str = "",
) -> dict[str, Any]:
    """Fetch unread turns.

    Thread-scoped ``mark_read`` stamps the seat from
    ``resolve_dispatch_from_agent`` (``mark_read_seat``). A missing reader
    or a ``to`` that is not that seat is an advisory, not a silent no-op.
    """
    if isinstance(thread, int):
        thread = str(thread)
    effective_to = to if to else None
    effective_thread = thread if thread else None
    if effective_to is None and effective_thread is None:
        return {"error": "fetch_unread requires at least one of: to, thread"}
    if effective_thread is None and effective_to is not None:
        return _fetch_unread_toc_impl(
            to=effective_to,
            mark_read=mark_read,
            active_since=active_since,
            limit=limit,
            all_threads=all_threads,
        )
    advisories: list[str] = []
    mark_read_seat: str | None = None
    apply_mark = mark_read
    if mark_read:
        reader, advisories, author_err, apply_mark = _resolve_mark_reader(
            op="fetch_unread",
            from_agent=from_agent,
            to_seat=effective_to,
        )
        if author_err is not None:
            return author_err
        mark_read_seat = reader if apply_mark else None
    result = _fetch_impl(
        to=effective_to,
        thread=effective_thread,
        last=None,
        unread=True,
        mark_read=apply_mark,
        compact=compact,
        mark_read_seat=mark_read_seat,
    )
    return _merge_advisories(result, advisories)


def _fetch_dispatch(
    *,
    to: str | None = None,
    thread: str | int | None = None,
    last: int = 10,
    unread: bool = False,
    mark_read: bool = False,
    compact: bool = False,
    all: bool = False,
    after_turn: int | None = None,
    from_agent: str = "",
) -> dict[str, Any]:
    """Dispatch wrapper for fetch — normalizes empty strings and resolves last/all/unread.

    ``mark_read`` stamps ``resolve_dispatch_from_agent``'s seat. When ``to``
    is a different seat the stamp is refused and the response names both.
    Omitting ``from_agent`` still stamps, with an advisory naming that seat.
    """
    if isinstance(thread, int):
        thread = str(thread)
    effective_to = to if to else None
    effective_thread = thread if thread else None
    if all or unread:
        effective_last = None
    else:
        effective_last = max(1, min(last, _FETCH_CONTEXT_CAP))
    mark_read_seat: str | None = None
    advisories: list[str] = []
    apply_mark = False
    if mark_read:
        reader, advisories, author_err, apply_mark = _resolve_mark_reader(
            op="fetch",
            from_agent=from_agent,
            to_seat=effective_to,
        )
        if author_err is not None:
            return author_err
        mark_read_seat = reader if apply_mark else None
    result = _fetch_impl(
        to=effective_to,
        thread=effective_thread,
        last=effective_last,
        unread=unread,
        mark_read=apply_mark,
        compact=compact,
        after_turn=after_turn,
        mark_read_seat=mark_read_seat,
    )
    return _merge_advisories(result, advisories)


def _get_dispatch(
    *,
    thread: str | int = "",
    turn_number: int | str = 0,
    mark_read: bool = False,
    from_agent: str = "",
) -> dict[str, Any]:
    if isinstance(thread, int):
        thread = str(thread)
    if not thread:
        return {"error": "get requires: thread (str)"}
    if turn_number in (0, "", None):
        return {
            "error": "get requires: turn_number (int >= 1 or 'latest')",
        }
    if turn_number != "latest":
        try:
            tn = int(turn_number)
        except (TypeError, ValueError):
            return {
                "error": "get requires: turn_number (int >= 1 or 'latest')",
            }
        if tn < 1:
            return {"error": "get requires: turn_number (int >= 1 or 'latest')"}
        turn_number = tn
    reader_agent = ""
    advisories: list[str] = []
    apply_mark = mark_read
    if mark_read:
        reader_agent_resolved, advisories, author_err, apply_mark = (
            _resolve_mark_reader(
                op="get",
                from_agent=from_agent,
                to_seat=None,
            )
        )
        if author_err is not None:
            return author_err
        reader_agent = reader_agent_resolved or ""
    result = _get_impl(
        thread=thread,
        turn_number=turn_number,
        mark_read=apply_mark,
        reader_agent=reader_agent,
    )
    return _merge_advisories(result, advisories)
