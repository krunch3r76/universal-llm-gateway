"""Fetch-family agent_bus dispatchers: fetch, fetch_unread, get."""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlencode

from agent_bus_store.recipients import turn_mark_read_eligible
from mcp_events import record

from .._agent_bus_author import UNSPECIFIED_AUTHOR, resolve_dispatch_from_agent
from ._closeout_projection import project_turn_body, project_turns_header
from ._shared import _FETCH_CONTEXT_CAP, _structured_relay_error, relay

logger = logging.getLogger(__name__)

_FETCH_PROJECTIONS = frozenset({"header", "compact", "full"})
_GET_TURN_NUMBERS_CAP = 10


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
    ``from_agent`` still marks when the resolved reader is a real seat, and
    the advisory names that seat. The omit sentinel ``unspecified`` is not a
    seat: stamping it would clear the phantom inbox instead of the caller,
    so the mark is skipped.
    """
    supplied = bool(from_agent.strip())
    reader, author_err = resolve_dispatch_from_agent(from_agent)
    if author_err is not None:
        return None, [], author_err, False
    advisories: list[str] = []
    if reader == UNSPECIFIED_AUTHOR:
        advisories.append(f"{op}: mark_read skipped — reader {reader!r} is not a seat")
        return None, advisories, None, False
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


def _resolve_fetch_projection(
    *,
    projection: str | None,
    compact: bool | None,
    mark_read: bool,
) -> tuple[str | None, dict[str, Any] | None]:
    """Return resolved projection mode or a structured error."""
    explicit: str | None = None
    if projection is not None:
        explicit = projection.strip().lower()
        if explicit not in _FETCH_PROJECTIONS:
            return None, {
                "error": f"fetch: unknown projection {projection!r}",
                "reason": "fetch_projection_invalid",
            }
    compact_mode: str | None = None
    if compact is True:
        compact_mode = "header"
    elif compact is False:
        compact_mode = "full"
    if compact is not None and explicit is not None and compact_mode != explicit:
        return None, {
            "error": (
                f"fetch: compact={compact!r} conflicts with projection={projection!r}"
            ),
            "reason": "fetch_projection_conflict",
        }
    if explicit is not None:
        mode = explicit
    elif compact is True:
        mode = "header"
    elif compact is False:
        mode = "full"
    elif mark_read:
        mode = "full"
    else:
        mode = "header"
    return mode, None


def _project_fetch_result(result: Any, mode: str) -> Any:
    """Apply header or compact shaping; full mode returns the relay payload unchanged."""
    if isinstance(result, dict) and "error" in result:
        return result
    if mode == "full":
        return result
    if isinstance(result, list):
        turns = result
        envelope: dict[str, Any] | None = None
    elif isinstance(result, dict):
        turns = result.get("turns", [])
        envelope = result
    else:
        return result
    if mode == "header":
        header_turns = project_turns_header(turns)
        body_hint = (
            'bodies: get(thread, turn_numbers=[…]) or fetch projection="full"'
        )
        if envelope is None:
            return {
                "selection": "headers",
                "projection": "header",
                "projection_available": ["compact", "full"],
                "body_hint": body_hint,
                "turns": header_turns,
            }
        out = {key: value for key, value in envelope.items() if key != "turns"}
        out["selection"] = "headers"
        out["projection"] = "header"
        out["projection_available"] = ["compact", "full"]
        out["body_hint"] = body_hint
        out["turns"] = header_turns
        return out
    projected = [
        project_turn_body(turn, projection="compact")
        if isinstance(turn, dict)
        else turn
        for turn in turns
    ]
    if envelope is None:
        return projected
    shaped = dict(envelope)
    shaped["turns"] = projected
    return shaped


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
    limit: int | None = None,
    resolved_projection: str,
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
    if limit is not None:
        params["limit"] = limit
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
        mode=resolved_projection,
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
    projection: str | None = None,
) -> dict[str, Any]:
    """Direct single-turn lookup via GET /turns/by-number."""
    qs = urlencode({"thread": thread, "turn_number": turn_number})
    result = relay("agent-bus", "GET", f"/turns/by-number?{qs}")
    if isinstance(result, dict) and "error" in result:
        structured = _structured_relay_error(result, op="get")
        if structured is not None:
            relay_reason = result.get("reason")
            if relay_reason and structured.get("reason") is None:
                structured["reason"] = relay_reason
            return structured
        return {"error": f"agent-bus error: {result['error']}"}
    advisories: list[str] = []
    if mark_read and isinstance(result, dict):
        to_agent = str(result.get("to") or result.get("to_agent") or "")
        if reader_agent and turn_mark_read_eligible(
            seat=reader_agent, to_agent=to_agent
        ):
            if result.get("read_at") is None:
                turn_id = result.get("id")
                if turn_id is not None:
                    read_qs = urlencode({"mark_read_seat": reader_agent})
                    patched = relay(
                        "agent-bus",
                        "PATCH",
                        f"/turns/{turn_id}/read?{read_qs}",
                    )
                    if isinstance(patched, dict) and patched.get("read_at"):
                        result = dict(result)
                        result["read_at"] = patched["read_at"]
        elif mark_read:
            advisories.append(
                "get: mark_read skipped — turn is not addressed to the caller "
                f"(to={to_agent!r}, reader={reader_agent!r})"
            )
    if isinstance(result, dict):
        result = project_turn_body(result, projection=projection)
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
    record_mode = "store_compact" if compact else "full"
    result = _fetch_impl(
        to=effective_to,
        thread=effective_thread,
        last=None,
        unread=True,
        mark_read=apply_mark,
        compact=compact,
        mark_read_seat=mark_read_seat,
        limit=limit,
        resolved_projection=record_mode,
    )
    return _merge_advisories(result, advisories)


def _fetch_dispatch(
    *,
    to: str | None = None,
    thread: str | int | None = None,
    last: int = 10,
    unread: bool = False,
    mark_read: bool = False,
    compact: bool | None = None,
    projection: str | None = None,
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
    mode, projection_err = _resolve_fetch_projection(
        projection=projection,
        compact=compact,
        mark_read=mark_read,
    )
    if projection_err is not None:
        return projection_err
    if mode is None:
        return {
            "error": "fetch: projection resolution failed",
            "reason": "fetch_projection_invalid",
        }
    relay_compact = mode == "header"
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
        compact=relay_compact,
        after_turn=after_turn,
        mark_read_seat=mark_read_seat,
        resolved_projection=mode,
    )
    result = _project_fetch_result(result, mode)
    return _merge_advisories(result, advisories)


def _dedupe_turn_numbers(turn_numbers: list[int]) -> list[int]:
    seen: set[int] = set()
    ordered: list[int] = []
    for number in turn_numbers:
        if number in seen:
            continue
        seen.add(number)
        ordered.append(number)
    return ordered


def _validate_turn_numbers(
    turn_numbers: list[Any],
) -> tuple[list[int] | None, dict[str, Any] | None]:
    picks: list[int] = []
    for item in turn_numbers:
        if isinstance(item, bool) or not isinstance(item, int):
            return None, {
                "error": "get: turn_numbers must be a list of int >= 1",
                "reason": "get_turn_numbers_invalid",
            }
        if item < 1:
            return None, {
                "error": "get: turn_numbers must be a list of int >= 1",
                "reason": "get_turn_numbers_invalid",
            }
        picks.append(item)
    deduped = _dedupe_turn_numbers(picks)
    if len(deduped) > _GET_TURN_NUMBERS_CAP:
        return None, {
            "error": f"get: turn_numbers cap is {_GET_TURN_NUMBERS_CAP}",
            "reason": "get_turn_numbers_over_cap",
        }
    return deduped, None


def _get_dispatch(
    *,
    thread: str | int = "",
    turn_number: int | str = 0,
    turn_numbers: list[int] | None = None,
    mark_read: bool = False,
    from_agent: str = "",
    projection: str | None = None,
) -> dict[str, Any]:
    if isinstance(thread, int):
        thread = str(thread)
    if not thread:
        return {"error": "get requires: thread (str)"}
    if turn_numbers is not None:
        if turn_number not in (0, "", None):
            return {
                "error": "get: pass either turn_number or turn_numbers, not both",
                "reason": "get_turn_selector_conflict",
            }
        picks, pick_err = _validate_turn_numbers(turn_numbers)
        if pick_err is not None:
            return pick_err
        if picks is None:
            return {
                "error": "get: turn_numbers validation failed",
                "reason": "get_turn_numbers_invalid",
            }
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
        turns_out: list[Any] = []
        partial = False
        for pick in picks:
            try:
                picked = _get_impl(
                    thread=thread,
                    turn_number=pick,
                    mark_read=apply_mark,
                    reader_agent=reader_agent,
                    projection=projection,
                )
            except Exception as exc:  # noqa: BLE001 — per-pick failure surface
                turns_out.append({"turn_number": pick, "error": str(exc)})
                partial = True
                continue
            if isinstance(picked, dict) and "error" in picked and "turn" not in picked:
                err_entry: dict[str, Any] = {
                    "turn_number": pick,
                    "error": picked["error"],
                }
                if picked.get("reason") is not None:
                    err_entry["reason"] = picked["reason"]
                turns_out.append(err_entry)
                partial = True
                continue
            if isinstance(picked, dict) and picked.get("argument_rewrite_advisory"):
                advisories.extend(
                    str(picked["argument_rewrite_advisory"]).split("; ")
                )
            turn = picked.get("turn") if isinstance(picked, dict) else picked
            if isinstance(turn, dict) and "error" in turn:
                err_entry = {"turn_number": pick, "error": turn["error"]}
                if turn.get("reason") is not None:
                    err_entry["reason"] = turn["reason"]
                turns_out.append(err_entry)
                partial = True
            else:
                turns_out.append(turn)
        payload: dict[str, Any] = {"turns": turns_out}
        if partial:
            payload["partial"] = True
        return _merge_advisories(payload, advisories)
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
        projection=projection,
    )
    return _merge_advisories(result, advisories)
