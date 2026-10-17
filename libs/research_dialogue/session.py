from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable

import httpx

from research_dialogue._types import SendResult, Snapshot
from research_dialogue.compose import (
    assert_no_forbidden_keys,
    build_messages_body,
    context_token_count,
)
from research_dialogue.excerpts import read_excerpts
from research_dialogue.contract import parse_contract
from research_dialogue.cost import billed_usd
from research_dialogue.ledger_read import check_ok, read_status
from research_dialogue.ledger_sqlite import ledger_max_id
from research_dialogue.models import normalize_ledger_model
from research_dialogue.retrieval import search as rag_search
from research_dialogue.transport import send_messages

TURN_LIMIT = 30
CONTEXT_CAP = 300_000


def _dec(val: Any) -> Decimal:
    return Decimal(str(val))


def _snap_dict(s: Snapshot) -> dict[str, Any]:
    return {
        "committed_usd": str(s.committed_usd),
        "open_reservations_usd": str(s.open_reservations_usd),
        "window_start": s.window_start,
        "ledger_instance_id": s.ledger_instance_id,
        "as_of_utc": s.as_of_utc.isoformat(),
    }


def _ledger_delta(committed_s0: Decimal, snap: Snapshot) -> Decimal:
    return (snap.committed_usd + snap.open_reservations_usd) - committed_s0


def _ledger_model(model: str) -> str:
    return normalize_ledger_model(model)


def _entries_for_model(entries: list[dict[str, Any]], model: str) -> list[dict[str, Any]]:
    want = _ledger_model(model)
    return [e for e in entries if e.get("model") == want]


def _pair_by_request_id(entries: list[dict[str, Any]], model: str) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in _entries_for_model(entries, model):
        rid = row.get("request_id")
        if not isinstance(rid, str):
            continue
        grouped.setdefault(rid, []).append(row)
    out: dict[str, dict[str, Any]] = {}
    for rid, rows in grouped.items():
        reserve = [r for r in rows if r.get("kind") == "reserve"]
        commit = [r for r in rows if r.get("kind") == "commit"]
        if reserve:
            out[rid] = {
                "reserve_usd": sum(_dec(r.get("usd")) for r in reserve),
                "commit_usd": sum(_dec(r.get("usd")) for r in commit) if commit else None,
                "commit_row": commit[0] if commit else None,
            }
    return out


def _classify_smoke(
    entries: list[dict[str, Any]],
    *,
    model: str,
    billed: Decimal,
    committed_rise: Decimal,
) -> str:
    """Return smoke_result: exact | floor | fail."""
    if billed <= 0 or committed_rise <= 0:
        return "fail"
    pairs = _pair_by_request_id(entries, model)
    for info in pairs.values():
        commit_usd = info.get("commit_usd")
        reserve_usd = info["reserve_usd"]
        if commit_usd is None:
            continue
        if abs(commit_usd - billed) <= Decimal("0.000001"):
            return "exact"
        if committed_rise >= billed and committed_rise == reserve_usd:
            return "floor"
    return "fail"


def _post_baseline(state: dict[str, Any]) -> tuple[Decimal | None, str | None, int | None]:
    lc = state.get("last_post_committed_usd")
    li = state.get("last_post_ledger_instance_id")
    lm = state.get("last_post_ledger_max_id")
    return (
        _dec(lc) if lc is not None else None,
        li if isinstance(li, str) and li else None,
        int(lm) if lm is not None else None,
    )


def _pre_ledger_max_id(snap: Snapshot) -> int | None:
    import sqlite3

    raw = snap.raw.get("ledger_path")
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        return ledger_max_id(Path(raw))
    except (OSError, sqlite3.Error):
        return None


def _store_post_baseline(state: dict[str, Any], post: Snapshot) -> None:
    state["last_post_committed_usd"] = str(post.committed_usd)
    state["last_post_ledger_instance_id"] = post.ledger_instance_id
    mid = _pre_ledger_max_id(post)
    if mid is not None:
        state["last_post_ledger_max_id"] = mid


def _closing_already_recorded(session_dir: Path, state: dict[str, Any]) -> bool:
    if state.get("closing_recorded"):
        return True
    for path in session_dir.glob("turn-*.json"):
        if path.name.endswith("-reply.txt"):
            continue
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if doc.get("closing"):
            return True
    return False


class SessionStore:
    def __init__(self, session_dir: Path) -> None:
        self.dir = session_dir
        self.dir.mkdir(parents=True, exist_ok=True)
        self.state_path = self.dir / "state.json"

    def load(self) -> dict[str, Any]:
        if not self.state_path.is_file():
            return {}
        return json.loads(self.state_path.read_text(encoding="utf-8"))

    def save(self, state: dict[str, Any]) -> None:
        self.state_path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")


def _invoke_send(
    send_fn: Callable[..., SendResult],
    body: dict[str, Any],
    *,
    stream: bool = False,
) -> SendResult:
    try:
        if send_fn is send_messages:
            return send_messages(body, stream=stream)
        return send_fn(body)
    except httpx.HTTPError as exc:
        resp = getattr(exc, "response", None)
        status_code = resp.status_code if resp is not None else None
        return SendResult(
            ok=False,
            status_code=status_code,
            text="",
            usage={},
            stop_reason=None,
            message_id=None,
            error_body=repr(exc),
        )


def wait_for_open_zero(
    s0: Snapshot,
    *,
    read_fn: Callable[[], Snapshot] = read_status,
    poll_s: int = 60,
    max_wait_s: int = 15 * 60,
) -> tuple[Snapshot, Decimal]:
    snap = s0
    open0 = snap.open_reservations_usd
    deadline = time.monotonic() + max_wait_s
    while snap.open_reservations_usd > 0 and time.monotonic() < deadline:
        time.sleep(poll_s)
        snap = read_fn()
    return snap, open0


def run_smoke(
    session_dir: Path,
    *,
    read_fn: Callable[[], Snapshot] = read_status,
    send_fn: Callable[..., SendResult] = send_messages,
    stream: bool = False,
) -> int:
    sb = read_fn()
    if check_ok(sb, sb) is not None:
        _write_smoke(
            session_dir,
            {"pass": False, "smoke_result": "fail", "reason": "pre_read_failed"},
        )
        return 2
    body = build_messages_body(
        turn_index=1,
        history=[],
        excerpt_text="smoke",
        minor_notes="smoke",
        smoke=True,
        model="claude-sonnet-5",
        max_tokens=64,
        stream=stream,
    )
    assert_no_forbidden_keys(body)
    sent = _invoke_send(send_fn, body, stream=stream)
    _write_smoke_partial(
        session_dir,
        send=sent,
        body=body,
    )
    sa = None
    post_reason: str | None = None
    billed = Decimal("0")
    missing: list[str] = []
    unknown: list[str] = []
    try:
        sa = read_fn()
        post_reason = check_ok(sa, sb)
        if sent.ok:
            billed, missing, unknown = billed_usd(sent.usage, model="claude-sonnet-5")
    except Exception as exc:  # noqa: BLE001 — merge failure into smoke.json
        post_reason = post_reason or f"post_read_failed:{type(exc).__name__}"
    payload: dict[str, Any] = {
        "send_ok": sent.ok,
        "status_code": sent.status_code,
        "error_body": sent.error_body,
        "usage": sent.usage,
        "usage_incomplete": sent.usage_incomplete,
        "billed_missing_fields": missing,
        "billed_unknown_fields": unknown,
        "post_read_failed": post_reason,
    }
    if sa is None:
        payload.update({"pass": False, "smoke_result": "fail", "reason": post_reason})
        _write_smoke(session_dir, payload)
        return 2
    if post_reason:
        payload.update({"pass": False, "smoke_result": "fail", "reason": post_reason})
        _write_smoke(session_dir, payload)
        return 2
    committed_b = sb.committed_usd
    committed_a = sa.committed_usd
    open_b = sb.open_reservations_usd
    open_a = sa.open_reservations_usd
    committed_rise = committed_a - committed_b
    smoke_result = _classify_smoke(
        sa.last_entries,
        model="claude-sonnet-5",
        billed=billed,
        committed_rise=committed_rise,
    )
    if not (sent.ok and open_a <= open_b):
        smoke_result = "fail"
    gap = str(committed_rise - billed) if committed_rise >= billed else None
    payload.update(
        {
            "pass": smoke_result == "exact",
            "smoke_result": smoke_result,
            "committed_b": str(committed_b),
            "committed_a": str(committed_a),
            "open_b": str(open_b),
            "open_a": str(open_a),
            "billed": str(billed),
            "committed_rise": str(committed_rise),
            "floor_gap": gap if smoke_result == "floor" else None,
        }
    )
    _write_smoke(session_dir, payload)
    return 0 if smoke_result == "exact" else 2


def _write_smoke(session_dir: Path, payload: dict[str, Any]) -> None:
    path = session_dir / "smoke.json"
    if path.is_file():
        prior = json.loads(path.read_text(encoding="utf-8"))
        prior.update(payload)
        payload = prior
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _write_smoke_partial(session_dir: Path, *, send: SendResult, body: dict[str, Any]) -> None:
    _write_smoke(
        session_dir,
        {
            "request": body,
            "response_text": send.text,
            "usage": send.usage,
            "stop_reason": send.stop_reason,
            "usage_skipped_dict_keys": send.usage_skipped_dict_keys,
        },
    )


def run_start(
    session_dir: Path,
    *,
    deliverable: Path,
    minor_notes: Path,
    read_fn: Callable[[], Snapshot] = read_status,
) -> None:
    store = SessionStore(session_dir)
    read_excerpts(deliverable)
    s0 = read_fn()
    s0, open0 = wait_for_open_zero(s0, read_fn=read_fn)
    state = {
        "committed_S0": str(s0.committed_usd),
        "open_reservations_at_start": str(open0),
        "window_start": s0.window_start,
        "ledger_instance_id": s0.ledger_instance_id,
        "deliverable": str(deliverable),
        "minor_notes": str(minor_notes),
        "turn_count": 0,
        "history": [],
        "running_billed": "0",
        "last_ledger": "",
        "last_reply_path": "",
        "last_response_at": None,
        "parse_fail_streak": 0,
        "stall_streak": 0,
        "last_ledger_text": "",
        "stop_reason": None,
        "s0_snapshot": _snap_dict(s0),
    }
    store.save(state)


def _default_challenge() -> str:
    return (
        "Pick one recommendation from your ledger and name the corpus passage "
        "that would falsify it if absent."
    )


def evaluate_stop_rules(
    state: dict[str, Any],
    *,
    pre_reason: str | None,
    send_result: SendResult | None,
    parsed_ok: bool,
    usage: dict[str, Any],
    converged: bool,
) -> str | None:
    if pre_reason:
        return pre_reason
    if send_result and not send_result.ok:
        if send_result.gate_refusal:
            return "gate_refusal"
        if send_result.status_code and 400 <= send_result.status_code < 600:
            return "gate_refusal"
        return "transport_error"
    if state.get("turn_count", 0) >= TURN_LIMIT:
        return "turn_limit"
    if context_token_count(usage) > CONTEXT_CAP:
        return "context_cap"
    if converged:
        return "converged"
    stop = (send_result.stop_reason if send_result else None) or ""
    if stop in {"refusal", "pause_turn"}:
        return "anomaly"
    if not parsed_ok:
        if state.get("parse_fail_streak", 0) >= 2:
            return "anomaly"
    if state.get("stall_streak", 0) >= 2:
        return "stall"
    return None


def run_turn(
    session_dir: Path,
    *,
    challenge_file: Path | None = None,
    read_fn: Callable[[], Snapshot] = read_status,
    send_fn: Callable[..., SendResult] = send_messages,
    search_fn: Callable[..., Any] = rag_search,
    stream: bool = False,
) -> tuple[int, dict[str, Any]]:
    store = SessionStore(session_dir)
    state = store.load()
    if not state:
        raise RuntimeError("session not started")
    if state.get("stop_reason"):
        out = {
            "stop_reason": state["stop_reason"],
            "turn": state.get("turn_count", 0),
            "send_skipped": True,
        }
        print(json.dumps(out))
        return 3, out
    if int(state.get("turn_count", 0)) >= TURN_LIMIT:
        out = {"stop_reason": "turn_limit", "turn": state.get("turn_count", 0), "send_skipped": True}
        print(json.dumps(out))
        return 3, out

    s0 = Snapshot(
        as_of_utc=datetime.now(tz=UTC),
        healthy=True,
        committed_usd=_dec(state["committed_S0"]),
        open_reservations_usd=Decimal("0"),
        window_start=state["window_start"],
        ledger_instance_id=state["ledger_instance_id"],
        latch_active=False,
    )
    pre = read_fn()
    last_committed, last_inst, last_max_id = _post_baseline(state)
    pre_reason = check_ok(
        pre,
        s0,
        last_post_committed=last_committed,
        last_post_ledger_instance_id=last_inst,
        last_post_ledger_max_id=last_max_id,
        pre_ledger_max_id=_pre_ledger_max_id(pre),
    )
    if pre_reason:
        state["stop_reason"] = pre_reason
        store.save(state)
        out = {"stop_reason": pre_reason, "turn": state.get("turn_count", 0), "send_skipped": True}
        print(json.dumps(out))
        return 3, out

    turn_index = int(state.get("turn_count", 0)) + 1
    history: list[dict[str, str]] = list(state.get("history") or [])
    retrieval_results: list[Any] = []
    dropped: list[Any] = []
    ledger_verbatim = state.get("last_ledger_text") or ""
    if turn_index >= 2 and ledger_verbatim:
        queries = state.get("pending_queries") or []
        for item in queries:
            if not isinstance(item, dict):
                continue
            scope = str(item.get("scope", ""))
            query = str(item.get("query", ""))
            if not scope or not query:
                continue
            res = search_fn(scope, query)
            if res.dropped:
                dropped.append(res)
            else:
                retrieval_results.append(res)

    challenge = _default_challenge()
    if challenge_file and challenge_file.is_file():
        challenge = challenge_file.read_text(encoding="utf-8")
    minor_notes = Path(state["minor_notes"]).read_text(encoding="utf-8")
    body = build_messages_body(
        turn_index=turn_index,
        history=history,
        deliverable_path=state["deliverable"],
        minor_notes=minor_notes,
        retrieval_results=retrieval_results,
        dropped_queries=dropped,
        ledger_verbatim=ledger_verbatim,
        challenge=challenge,
        stream=stream,
    )
    assert_no_forbidden_keys(body)
    send_result = _invoke_send(send_fn, body, stream=stream)
    _write_turn_json(
        session_dir,
        turn_index=turn_index,
        body=body,
        send_result=send_result,
        billed=Decimal("0"),
        running=_dec(state.get("running_billed", "0")),
        ledger_delta=Decimal("0"),
        pre=pre,
        post=pre,
        post_reason=None,
        matched=None,
        retrieval=(*retrieval_results, *dropped),
        closing=False,
        partial=True,
    )

    post = pre
    post_reason: str | None = None
    billed = Decimal("0")
    missing: list[str] = []
    unknown: list[str] = []
    running = _dec(state.get("running_billed", "0"))
    ledger_delta = Decimal("0")
    matched = None
    try:
        post = read_fn()
        post_reason = check_ok(
            post,
            s0,
            last_post_committed=last_committed,
            last_post_ledger_instance_id=last_inst,
            last_post_ledger_max_id=last_max_id,
            pre_ledger_max_id=_pre_ledger_max_id(post),
        )
        if send_result.ok:
            billed, missing, unknown = billed_usd(send_result.usage, model=body["model"])
        running = _dec(state.get("running_billed", "0")) + billed
        committed_s0 = _dec(state["committed_S0"])
        ledger_delta = _ledger_delta(committed_s0, post)
        if send_result.ok:
            pairs = _pair_by_request_id(post.last_entries, body["model"])
            for info in pairs.values():
                if info.get("commit_row"):
                    matched = info["commit_row"]
                    break
    except Exception as exc:  # noqa: BLE001
        post_reason = post_reason or f"post_read_failed:{type(exc).__name__}"

    reply_path = session_dir / f"turn-{turn_index:02d}-reply.txt"
    reply_path.write_text(send_result.text, encoding="utf-8")

    parsed = parse_contract(send_result.text) if send_result.ok else parse_contract("")
    if parsed.ok:
        state["parse_fail_streak"] = 0
        state["pending_queries"] = [{"scope": s, "query": q} for s, q in parsed.next_queries]
        if parsed.ledger.strip() == (state.get("last_ledger_text") or "").strip():
            state["stall_streak"] = int(state.get("stall_streak", 0)) + 1
        else:
            state["stall_streak"] = 0
        state["last_ledger_text"] = parsed.ledger
    else:
        state["parse_fail_streak"] = int(state.get("parse_fail_streak", 0)) + 1

    _write_turn_json(
        session_dir,
        turn_index=turn_index,
        body=body,
        send_result=send_result,
        billed=billed,
        running=running,
        ledger_delta=ledger_delta,
        pre=pre,
        post=post,
        post_reason=post_reason,
        matched=matched,
        retrieval=(*retrieval_results, *dropped),
        closing=False,
        partial=False,
        billed_missing=missing,
        billed_unknown=unknown,
    )

    if send_result.ok:
        last_user = body["messages"][-1]["content"]
        if isinstance(last_user, list) and last_user:
            user_text = str(last_user[0].get("text", ""))
        else:
            user_text = str(last_user)
        history.append({"role": "user", "content": user_text})
        history.append({"role": "assistant", "content": send_result.text})
        state["history"] = history
    state["turn_count"] = turn_index
    state["running_billed"] = str(running)
    state["last_reply_path"] = str(reply_path)
    state["last_response_at"] = datetime.now(tz=UTC).isoformat()
    state["final_post_snapshot"] = _snap_dict(post)
    _store_post_baseline(state, post)

    converged = parsed.ok and parsed.status.lower().startswith("converged")
    stop = evaluate_stop_rules(
        state,
        pre_reason=None,
        send_result=send_result,
        parsed_ok=parsed.ok,
        usage=send_result.usage,
        converged=converged,
    )
    if stop:
        state["stop_reason"] = stop
    store.save(state)

    line = {
        "turn": turn_index,
        "billed": str(billed),
        "running_sum": str(running),
        "ledger_delta": str(ledger_delta),
        "researcher_status": parsed.status if parsed.ok else "parse_error",
        "stop_reason": stop,
        "reply_path": str(reply_path),
    }
    print(json.dumps(line))
    return 0, line


def _write_turn_json(
    session_dir: Path,
    *,
    turn_index: int,
    body: dict[str, Any],
    send_result: SendResult,
    billed: Decimal,
    running: Decimal,
    ledger_delta: Decimal,
    pre: Snapshot,
    post: Snapshot,
    post_reason: str | None,
    matched: dict[str, Any] | None,
    retrieval: tuple[Any, ...] = (),
    closing: bool,
    partial: bool = False,
    billed_missing: list[str] | None = None,
    billed_unknown: list[str] | None = None,
) -> None:
    turn_doc: dict[str, Any] = {
        "turn": turn_index,
        "closing": closing,
        "request": body,
        "response_text": send_result.text,
        "usage": send_result.usage,
        "stop_reason": send_result.stop_reason,
        "send_ok": send_result.ok,
        "status_code": send_result.status_code,
        "error_body": send_result.error_body,
        "usage_incomplete": send_result.usage_incomplete,
        "delta_usage_seen": send_result.delta_usage_seen,
        "parse_error": send_result.parse_error,
        "usage_skipped_dict_keys": send_result.usage_skipped_dict_keys,
    }
    if partial:
        (session_dir / f"turn-{turn_index:02d}.json").write_text(
            json.dumps(turn_doc, indent=2) + "\n",
            encoding="utf-8",
        )
        return
    turn_doc.update(
        {
            "billed_usd": str(billed),
            "billed_missing_fields": billed_missing or [],
            "billed_unknown_fields": billed_unknown or [],
            "running_sum": str(running),
            "ledger_delta": str(ledger_delta),
            "post_open_usd": str(post.open_reservations_usd),
            "pre_snapshot": _snap_dict(pre),
            "post_snapshot": _snap_dict(post),
            "post_read_failed": post_reason,
            "matched_commit": matched,
        }
    )
    if retrieval:
        turn_doc["retrieval"] = [
            {
                "search_id": r.search_id,
                "scope": r.scope,
                "weak_match": r.weak_match,
                "rerank_status": r.rerank_status,
                "top_ce": r.top_ce,
                "dropped": r.dropped,
            }
            for r in retrieval
        ]
    (session_dir / f"turn-{turn_index:02d}.json").write_text(
        json.dumps(turn_doc, indent=2) + "\n", encoding="utf-8"
    )


def run_close(
    session_dir: Path,
    *,
    stop_reason: str,
    no_p4: bool = False,
    read_fn: Callable[[], Snapshot] = read_status,
    send_fn: Callable[..., SendResult] = send_messages,
    stream: bool = False,
) -> tuple[int, dict[str, Any]]:
    store = SessionStore(session_dir)
    state = store.load()
    if _closing_already_recorded(session_dir, state):
        out = {"stop_reason": "closing_already_recorded", "send_skipped": True}
        print(json.dumps(out))
        return 3, out
    p4_text = ""
    session_line: dict[str, Any] = {"mode": "close", "stop_reason": stop_reason}
    s0 = Snapshot(
        as_of_utc=datetime.now(tz=UTC),
        healthy=True,
        committed_usd=_dec(state["committed_S0"]),
        open_reservations_usd=Decimal("0"),
        window_start=state["window_start"],
        ledger_instance_id=state["ledger_instance_id"],
        latch_active=False,
    )
    last_committed, last_inst, last_max_id = _post_baseline(state)
    if not no_p4:
        pre = read_fn()
        pre_reason = check_ok(
            pre,
            s0,
            last_post_committed=last_committed,
            last_post_ledger_instance_id=last_inst,
            last_post_ledger_max_id=last_max_id,
            pre_ledger_max_id=_pre_ledger_max_id(pre),
        )
        if pre_reason:
            out = {"stop_reason": pre_reason, "send_skipped": True}
            print(json.dumps(out))
            return 3, out
        turn_index = int(state.get("turn_count", 0)) + 1
        history = list(state.get("history") or [])
        body = build_messages_body(
            turn_index=turn_index,
            history=history,
            closing=True,
            stop_reason=stop_reason,
            stream=stream,
        )
        assert_no_forbidden_keys(body)
        send_result = _invoke_send(send_fn, body, stream=stream)
        _write_turn_json(
            session_dir,
            turn_index=turn_index,
            body=body,
            send_result=send_result,
            billed=Decimal("0"),
            running=_dec(state.get("running_billed", "0")),
            ledger_delta=Decimal("0"),
            pre=pre,
            post=pre,
            post_reason=None,
            matched=None,
            closing=True,
            partial=True,
        )
        post = pre
        post_reason = None
        billed = Decimal("0")
        missing: list[str] = []
        unknown: list[str] = []
        running = _dec(state.get("running_billed", "0"))
        ledger_delta = Decimal("0")
        matched = None
        try:
            post = read_fn()
            post_reason = check_ok(
                post,
                s0,
                last_post_committed=last_committed,
                last_post_ledger_instance_id=last_inst,
                last_post_ledger_max_id=last_max_id,
                pre_ledger_max_id=_pre_ledger_max_id(post),
            )
            if send_result.ok:
                billed, missing, unknown = billed_usd(send_result.usage, model=body["model"])
            running = _dec(state.get("running_billed", "0")) + billed
            ledger_delta = _ledger_delta(_dec(state["committed_S0"]), post)
            if send_result.ok:
                pairs = _pair_by_request_id(post.last_entries, body["model"])
                for info in pairs.values():
                    if info.get("commit_row"):
                        matched = info["commit_row"]
                        break
        except Exception as exc:  # noqa: BLE001
            post_reason = post_reason or f"post_read_failed:{type(exc).__name__}"
        _write_turn_json(
            session_dir,
            turn_index=turn_index,
            body=body,
            send_result=send_result,
            billed=billed,
            running=running,
            ledger_delta=ledger_delta,
            pre=pre,
            post=post,
            post_reason=post_reason,
            matched=matched,
            closing=True,
            partial=False,
            billed_missing=missing,
            billed_unknown=unknown,
        )
        p4_text = send_result.text
        reply_path = session_dir / f"turn-{turn_index:02d}-reply.txt"
        reply_path.write_text(send_result.text, encoding="utf-8")
        state["turn_count"] = turn_index
        state["running_billed"] = str(running)
        state["final_post_snapshot"] = _snap_dict(post)
        state["stop_reason"] = stop_reason
        state["closing_recorded"] = True
        _store_post_baseline(state, post)
        store.save(state)
        session_line = {
            "mode": "close",
            "turn": turn_index,
            "billed": str(billed),
            "running_sum": str(running),
            "ledger_delta": str(ledger_delta),
            "researcher_status": "closing",
            "stop_reason": stop_reason,
            "reply_path": str(reply_path),
            "send_ok": send_result.ok,
        }
    state = store.load()
    _write_design_note(session_dir, state, stop_reason, p4_text)
    _write_session_md(session_dir, state, stop_reason)
    _write_transcript(session_dir, state)
    session_line.setdefault("turns", state.get("turn_count"))
    session_line.setdefault("running_billed", state.get("running_billed"))
    return 0, session_line


def _write_design_note(
    session_dir: Path,
    state: dict[str, Any],
    stop_reason: str,
    p4_text: str,
) -> None:
    lines = [p4_text, "", "## Cost table", ""]
    lines.append(
        "| turn | model | input | cache-write | cache-read | output | billed | running | ledger Δ |"
    )
    lines.append("|---:|---|---:|---:|---:|---:|---:|---:|---:|")
    for path in sorted(session_dir.glob("turn-*.json")):
        if path.name.endswith("-reply.txt"):
            continue
        doc = json.loads(path.read_text(encoding="utf-8"))
        u = doc.get("usage") or {}
        lines.append(
            "| {turn} | {model} | {inp} | {cw} | {cr} | {out} | {bill} | {run} | {ld} |".format(
                turn=doc.get("turn"),
                model=(doc.get("request") or {}).get("model"),
                inp=u.get("input_tokens", 0),
                cw=u.get("cache_creation_input_tokens", 0),
                cr=u.get("cache_read_input_tokens", 0),
                out=u.get("output_tokens", 0),
                bill=doc.get("billed_usd"),
                run=doc.get("running_sum", ""),
                ld=doc.get("ledger_delta", ""),
            )
        )
    final_post = state.get("final_post_snapshot") or {}
    final_committed = _dec(final_post.get("committed_usd", state.get("committed_S0", "0")))
    final_open = _dec(final_post.get("open_reservations_usd", "0"))
    committed_s0 = _dec(state.get("committed_S0", "0"))
    final_ledger_delta = (final_committed + final_open) - committed_s0
    harness_sum = _dec(state.get("running_billed", "0"))
    diff = harness_sum - final_ledger_delta
    lines.extend(
        [
            "",
            "## Totals",
            f"committed_S0: {committed_s0}",
            f"final_committed_usd: {final_committed}",
            f"final_open_reservations_usd: {final_open}",
            f"final_ledger_delta: {final_ledger_delta}",
            f"harness_running_sum: {harness_sum}",
            f"harness_minus_ledger_delta: {diff}",
            f"stop_reason: {stop_reason}",
            f"turns: {state.get('turn_count')}",
        ]
    )
    (session_dir / "design-note.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_session_md(session_dir: Path, state: dict[str, Any], stop_reason: str) -> None:
    text = "\n".join(
        [
            "# SESSION",
            f"stop_reason: {stop_reason}",
            f"turns: {state.get('turn_count')}",
            f"committed_S0: {state.get('committed_S0')}",
            f"running_billed: {state.get('running_billed')}",
            f"last_ledger: {state.get('last_ledger_text', '')}",
        ]
    )
    (session_dir / "SESSION.md").write_text(text + "\n", encoding="utf-8")


def _write_transcript(session_dir: Path, state: dict[str, Any]) -> None:
    parts = ["# Transcript", ""]
    for path in sorted(session_dir.glob("turn-*-reply.txt")):
        parts.append(f"## {path.name}")
        parts.append(path.read_text(encoding="utf-8"))
        parts.append("")
    (session_dir / "transcript.md").write_text("\n".join(parts) + "\n", encoding="utf-8")
