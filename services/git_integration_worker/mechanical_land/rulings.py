"""RULING ingest for mechanical-land (spec §7)."""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from agent_bus_store.db.turns import create_turn, get_turns
from universal_logging import get_logger

from services.git_integration_worker.mechanical_land.store import MechanicalLandStore

logger = get_logger(__name__)

_RULING_RE = re.compile(
    r"(?m)^RULING:\s*(HOLD|RESUME|BLOCK|WAIVE_GATE)\s+run=(?P<run_id>\S+)"
    r"(?:\s+step=(?P<step>\S+))?(?:\s+attempt=(?P<attempt>\d+))?"
    r"(?:\s+reason=(?P<reason>.+))?\s*$"
)
_NOTICE_FROM = "git-integration-worker"
_RULING_THREADS = ("12286",)


def parse_ruling_line(text: str, *, run_id: str) -> dict[str, Any] | None:
    """Return parsed ruling fields when *text* contains a canonical line for *run_id*."""
    for match in _RULING_RE.finditer(text):
        if match.group("run_id") != run_id:
            continue
        attempt_raw = match.group("attempt")
        return {
            "verb": match.group(1),
            "step": match.group("step"),
            "attempt": int(attempt_raw) if attempt_raw else None,
            "reason": (match.group("reason") or "").strip() or None,
            "line": match.group(0).strip(),
        }
    return None


def _turn_body(turn: dict[str, Any]) -> str:
    body = turn.get("body")
    return body if isinstance(body, str) else ""


def _mentions_run(body: str, run_id: str) -> bool:
    return run_id in body


def ingest_rulings_for_run(
    store: MechanicalLandStore,
    *,
    run_id: str,
    lane_thread_id: str,
    ruling_authors: list[str],
    post_notice_fn: Callable[..., tuple[int, str]] | None = None,
) -> int:
    """Fetch unread turns, fold canonical RULING lines, reply NOTICE on prose."""
    post_notice = post_notice_fn or _default_post_notice
    appended = 0
    threads = [lane_thread_id, *_RULING_THREADS]
    seen: set[tuple[str, int]] = set()
    for thread_id in threads:
        try:
            turns = get_turns(
                thread=thread_id,
                to="git-integration-worker",
                unread=True,
                mark_read=False,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "ruling fetch_unread failed run_id=%s thread=%s: %s",
                run_id,
                thread_id,
                exc,
            )
            continue
        for turn in turns:
            turn_no = int(turn.get("turn_number") or 0)
            key = (thread_id, turn_no)
            if key in seen:
                continue
            seen.add(key)
            from_agent = str(turn.get("from_agent") or "")
            if from_agent not in ruling_authors:
                continue
            body = _turn_body(turn)
            parsed = parse_ruling_line(body, run_id=run_id)
            snap = store.get_run(run_id)
            if snap is None:
                return appended
            tip = int(snap["state"].get("tip_seq") or 0)
            if parsed is not None:
                store.append_event(
                    run_id=run_id,
                    prev_seq=tip,
                    step_id=str(snap["state"].get("step") or "run"),
                    attempt=int(snap["state"].get("attempt") or 1),
                    flow="main",
                    event="ruling_folded",
                    payload={
                        "verb": parsed["verb"],
                        "step": parsed["step"],
                        "reason": parsed["reason"],
                        "bus_thread": thread_id,
                        "turn": turn_no,
                    },
                    source="bus_ruling",
                )
                appended += 1
                if parsed["verb"] == "RESUME":
                    snap = store.get_run(run_id)
                    if snap is not None:
                        tip = int(snap["state"].get("tip_seq") or 0)
                        store.append_event(
                            run_id=run_id,
                            prev_seq=tip,
                            step_id=str(snap["state"].get("step") or "run"),
                            attempt=int(snap["state"].get("attempt") or 1),
                            flow="main",
                            event="resumed",
                            payload={"by_ruling_seq": tip + 1},
                            source="bus_ruling",
                        )
                get_turns(
                    thread=thread_id,
                    to="git-integration-worker",
                    unread=True,
                    mark_read=True,
                    mark_read_seat="git-integration-worker",
                    after_turn=turn_no - 1 if turn_no > 0 else None,
                    last=1,
                )
            elif _mentions_run(body, run_id):
                store.append_event(
                    run_id=run_id,
                    prev_seq=tip,
                    step_id=str(snap["state"].get("step") or "run"),
                    attempt=int(snap["state"].get("attempt") or 1),
                    flow="main",
                    event="ruling_ignored",
                    payload={
                        "bus_thread": thread_id,
                        "turn": turn_no,
                        "why": "non_canonical_ruling_grammar",
                    },
                    source="bus_ruling",
                )
                appended += 1
                post_notice(
                    thread_id=thread_id,
                    run_id=run_id,
                    turn_number=turn_no,
                )
                get_turns(
                    thread=thread_id,
                    to="git-integration-worker",
                    unread=True,
                    mark_read=True,
                    mark_read_seat="git-integration-worker",
                    after_turn=turn_no - 1 if turn_no > 0 else None,
                    last=1,
                )
    return appended


def _default_post_notice(
    *,
    thread_id: str,
    run_id: str,
    turn_number: int,
) -> tuple[int, str]:
    grammar = (
        "RULING: <HOLD|RESUME|BLOCK|WAIVE_GATE> run=<run_id> "
        "[step=<step_id>] [attempt=<n>] [reason=<text>]"
    )
    body = (
        f"TYPE: NOTICE\n"
        f"run: {run_id}\n"
        f"why: non_canonical_ruling_grammar\n"
        f"grammar: {grammar}\n"
        f"ref: agent-bus:{thread_id}#{turn_number}\n"
    )
    try:
        _thread, _turn_id, _ts, turn_no, _marked = create_turn(
            thread_id=thread_id,
            from_agent=_NOTICE_FROM,
            to_agent="web-anthropic",
            subject=f"NOTICE|mechanical-land|ruling_ignored|{run_id}",
            body=body,
        )
        return turn_no, str(_turn_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("ruling_ignored notice failed thread=%s: %s", thread_id, exc)
        return 0, ""
