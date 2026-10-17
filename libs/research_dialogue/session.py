from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable

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


def _match_commit(
    entries: list[dict[str, Any]],
    *,
    model: str,
    billed: Decimal,
    t0: datetime,
    t1: datetime,
) -> dict[str, Any] | None:
    for row in entries:
        if row.get("kind") != "commit":
            continue
        if row.get("model") != model and row.get("model") != f"anthropic/{model}":
            continue
        usd = _dec(row.get("usd"))
        if abs(usd - billed) <= Decimal("0.000001"):
            created = row.get("created_at")
            if isinstance(created, str):
                try:
                    ts = datetime.fromisoformat(created.replace("Z", "+00:00"))
                    if t0 <= ts <= t1:
                        return row
                except ValueError:
                    continue
    return None


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
    send_fn: Callable[[dict[str, Any]], SendResult] = send_messages,
) -> int:
    store = SessionStore(session_dir)
    sb = read_fn()
    if check_ok(sb, sb) is not None:
        _write_smoke(session_dir, {"pass": False, "reason": "pre_read_failed"})
        return 2
    body = {
        "model": "claude-sonnet-5",
        "messages": [{"role": "user", "content": "Reply with the single word OK."}],
        "max_tokens": 64,
        "stream": True,
    }
    sent = send_fn(body)
    sa = read_fn()
    reason = check_ok(sa, sb)
    if reason:
        _write_smoke(session_dir, {"pass": False, "reason": reason})
        return 2
    billed = billed_usd(sent.usage, model="claude-sonnet-5")
    committed_b = sb.committed_usd
    committed_a = sa.committed_usd
    open_b = sb.open_reservations_usd
    open_a = sa.open_reservations_usd
    ok = (
        committed_a - committed_b >= billed
        and open_a <= open_b
    )
    matched = _match_commit(
        sa.last_entries,
        model="claude-sonnet-5",
        billed=billed,
        t0=sb.as_of_utc,
        t1=sa.as_of_utc,
    )
    if ok and matched is None and (committed_a - committed_b) > Decimal("0"):
        ok = abs((committed_a - committed_b) - billed) <= Decimal("0.000001")
    payload = {
        "pass": ok,
        "committed_b": str(committed_b),
        "committed_a": str(committed_a),
        "open_b": str(open_b),
        "open_a": str(open_a),
        "billed": str(billed),
        "matched_commit": matched,
    }
    _write_smoke(session_dir, payload)
    return 0 if ok else 2


def _write_smoke(session_dir: Path, payload: dict[str, Any]) -> None:
    (session_dir / "smoke.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


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
    if send_result and send_result.gate_refusal:
        return "gate_refusal"
    if send_result and not send_result.ok and send_result.status_code:
        if 400 <= send_result.status_code < 600:
            return "gate_refusal"
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
    send_fn: Callable[[dict[str, Any]], SendResult] = send_messages,
    search_fn: Callable[..., Any] = rag_search,
) -> tuple[int, dict[str, Any]]:
    store = SessionStore(session_dir)
    state = store.load()
    if not state:
        raise RuntimeError("session not started")
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
    prev_at = None
    if state.get("last_response_at"):
        prev_at = datetime.fromisoformat(state["last_response_at"])
    pre_reason = check_ok(pre, s0, previous_response_at=prev_at)
    if pre_reason:
        state["stop_reason"] = pre_reason
        store.save(state)
        out = {"stop_reason": pre_reason, "turn": state.get("turn_count", 0)}
        print(json.dumps(out))
        return 3, out

    turn_index = int(state.get("turn_count", 0)) + 1
    history: list[dict[str, str]] = list(state.get("history") or [])
    retrieval_results: list[Any] = []
    dropped: list[Any] = []
    ledger_verbatim = state.get("last_ledger_text") or ""
    if turn_index >= 2 and ledger_verbatim:
        queries = state.get("pending_queries") or []
        for scope, query in queries:
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
    )
    assert_no_forbidden_keys(body)
    send_result = send_fn(body)
    post = read_fn()
    post_reason = check_ok(post, s0)
    model = body["model"]
    billed = billed_usd(send_result.usage, model=model) if send_result.ok else Decimal("0")
    running = _dec(state.get("running_billed", "0")) + billed
    committed_s0 = _dec(state["committed_S0"])
    ledger_delta = _ledger_delta(committed_s0, post)

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

    matched = None
    if send_result.ok:
        matched = _match_commit(
            post.last_entries,
            model=model,
            billed=billed,
            t0=pre.as_of_utc,
            t1=post.as_of_utc,
        )

    turn_doc = {
        "turn": turn_index,
        "request": body,
        "response_text": send_result.text,
        "usage": send_result.usage,
        "stop_reason": send_result.stop_reason,
        "billed_usd": str(billed),
        "pre_snapshot": _snap_dict(pre),
        "post_snapshot": _snap_dict(post),
        "post_read_failed": post_reason,
        "matched_commit": matched,
        "retrieval": [
            {
                "search_id": r.search_id,
                "scope": r.scope,
                "weak_match": r.weak_match,
                "rerank_status": r.rerank_status,
                "top_ce": r.top_ce,
                "dropped": r.dropped,
            }
            for r in (*retrieval_results, *dropped)
        ],
    }
    (session_dir / f"turn-{turn_index:02d}.json").write_text(
        json.dumps(turn_doc, indent=2) + "\n", encoding="utf-8"
    )

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


def run_close(
    session_dir: Path,
    *,
    stop_reason: str,
    no_p4: bool = False,
    read_fn: Callable[[], Snapshot] = read_status,
    send_fn: Callable[[dict[str, Any]], SendResult] = send_messages,
) -> None:
    store = SessionStore(session_dir)
    state = store.load()
    p4_text = ""
    if not no_p4:
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
        if check_ok(pre, s0) is None:
            history = list(state.get("history") or [])
            body = build_messages_body(
                turn_index=int(state.get("turn_count", 0)) + 1,
                history=history,
                closing=True,
                stop_reason=stop_reason,
            )
            assert_no_forbidden_keys(body)
            result = send_fn(body)
            p4_text = result.text
    _write_design_note(session_dir, state, stop_reason, p4_text)
    _write_session_md(session_dir, state, stop_reason)
    _write_transcript(session_dir, state)


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
            "| {turn} | {model} | {inp} | {cw} | {cr} | {out} | {bill} | | |".format(
                turn=doc.get("turn"),
                model=(doc.get("request") or {}).get("model"),
                inp=u.get("input_tokens", 0),
                cw=u.get("cache_creation_input_tokens", 0),
                cr=u.get("cache_read_input_tokens", 0),
                out=u.get("output_tokens", 0),
                bill=doc.get("billed_usd"),
            )
        )
    lines.extend(
        [
            "",
            f"committed_S0: {state.get('committed_S0')}",
            f"stop_reason: {stop_reason}",
            f"running_billed: {state.get('running_billed')}",
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
