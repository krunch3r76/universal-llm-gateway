"""Auto-resume a cursor-sdk dispatch when its outstanding CDP replies land.

Friction 34156: a non-conductor dispatch that terminalizes with CDP generates
still unanswered is parked (``park_kind=await_cdp_reply``) without changing
its ordinary terminal status. The periodic reactor (and an immediate pass from
the closeout hook when replies already sit unconsumed on the bus) admits
exactly one ``resume_of`` child once every awaited generate has landed, failed,
or timed out. Conductor rows keep the R1 park-harvest path.

R1 (operator, 2026-10-03): "already harvested" means the bridge observed the
agent receive the qualifying reply (wait/get/fetch result), not merely that
the reply turn exists on the bus. Landed-but-unconsumed ⇒ park and resume at
once with the reply text.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from universal_logging import get_logger

from services.git_integration_worker.config import WorkerConfig
from services.git_integration_worker.cursor_bus import CursorBusClient
from services.git_integration_worker.cursor_dispatch_ledger import (
    CursorDispatchLedger,
)
from services.git_integration_worker.cursor_sdk_await_reply_events import (
    emit_sdk_await_reply_expired,
    emit_sdk_await_reply_parked,
    emit_sdk_await_reply_resume_admitted,
    emit_sdk_await_reply_resume_refused,
)
from services.git_integration_worker.cursor_sdk_await_reply_gate import (
    PARK_KIND_AWAIT_REPLY,
    RECONCILE_CHILD_STATUSES_SQL,
)
from services.git_integration_worker.cursor_sdk_context import steer_spool_dir
from services.git_integration_worker.cursor_sdk_packet import _IMPLEMENT_PREAMBLE
from services.git_integration_worker.cursor_sdk_park_ledger import (
    ParkRow,
    bump_resume_attempt,
    load_park_row,
    mark_park_expired,
    mark_park_resumed,
    record_resume_refusal,
)
from services.git_integration_worker.cursor_sdk_resume import (
    resume_eligibility_reason,
)
from services.git_integration_worker.models.cursor_api import CursorDispatchRequest

logger = get_logger(__name__)

AWAIT_REPLY_FLAG_ENV = "CURSOR_SDK_AWAIT_CDP_REPLY_RESUME"
AWAIT_REPLY_TTL_ENV = "CURSOR_SDK_AWAIT_CDP_REPLY_TTL_S"
ADMITTED_VIA_AWAIT_RESUME = "giw_await_reply_resume"
AWAIT_REPLY_PREAMBLE_VERSION = 1
_DEFAULT_TTL_S = 7200
_MAX_RESUME_ATTEMPTS = 10
_REFUSAL_GRACE_S = 300
_LEDGER_SUFFIX = ".cdp-generates.jsonl"
_RECORD_KEY = "park"
_PERMANENT_INELIGIBLE = frozenset(
    {
        "parent_missing",
        "sdk_agent_id_missing",
        "state_root_missing",
        "state_root_absent_on_disk",
    }
)
_REPLY_SUBJECT_RE = re.compile(
    r"^cdp (reply|FAILED|UNVERIFIED) — (?P<head>[0-9a-fA-F-]{8})",
    re.IGNORECASE,
)
_OUTCOME_BY_TOKEN = {
    "reply": "reply",
    "failed": "failed",
    "unverified": "unverified",
}

BusTurnsFn = Callable[[str, int], list[dict[str, Any]]]


@dataclass(frozen=True, slots=True)
class AwaitedGenerate:
    execution_id: str
    thread_id: str
    after_turn: int
    from_agent: str
    model: str | None
    fired_at: str


@dataclass(frozen=True, slots=True)
class ReplyHit:
    execution_id: str
    thread_id: str
    turn_number: int
    outcome: str
    body: str
    subject: str


@dataclass(slots=True)
class AwaitResumeSummary:
    admitted: list[tuple[str, str]] = field(default_factory=list)
    refused: list[tuple[str, str]] = field(default_factory=list)
    expired: list[str] = field(default_factory=list)
    reconciled: list[tuple[str, str]] = field(default_factory=list)
    waiting: list[str] = field(default_factory=list)
    skipped_conductor: list[str] = field(default_factory=list)


def await_reply_enabled() -> bool:
    # Default on after done items 1–5 pass (friction 34156 last increment).
    raw = os.environ.get(AWAIT_REPLY_FLAG_ENV, "1").strip().lower()
    return raw in ("1", "true", "yes", "on")


def await_reply_ttl_s() -> int:
    raw = os.environ.get(AWAIT_REPLY_TTL_ENV, "").strip()
    if raw:
        return max(0, int(raw))
    return _DEFAULT_TTL_S


def _now_dt() -> datetime:
    return datetime.now(UTC)


def _now() -> str:
    return _now_dt().isoformat()


def _ledger_path(spool_dir: Path | str, dispatch_id: str) -> Path:
    safe = dispatch_id.replace("/", "_").replace(":", "_")
    return Path(spool_dir) / f"{safe}{_LEDGER_SUFFIX}"


def _record_data(record_json: str | None) -> dict[str, Any]:
    try:
        data = json.loads(record_json or "{}")
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _dump(data: dict[str, Any]) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"))


def fired_generates(
    dispatch_id: str, *, spool_dir: Path | str | None = None
) -> list[AwaitedGenerate]:
    """Bridge fire rows for *dispatch_id*, oldest first; malformed lines skipped."""
    root = Path(spool_dir) if spool_dir is not None else steer_spool_dir()
    path = _ledger_path(root, dispatch_id)
    if not path.is_file():
        return []
    out: list[AwaitedGenerate] = []
    seen: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(parsed, dict):
            continue
        if str(parsed.get("kind") or "") == "received":
            continue
        execution_id = parsed.get("execution_id")
        thread_id = parsed.get("thread_id")
        if not isinstance(execution_id, str) or not execution_id:
            continue
        if not isinstance(thread_id, str) or not thread_id:
            continue
        if execution_id in seen:
            continue
        seen.add(execution_id)
        try:
            after_turn = int(parsed.get("after_turn", 0) or 0)
        except (TypeError, ValueError):
            after_turn = 0
        out.append(
            AwaitedGenerate(
                execution_id=execution_id,
                thread_id=str(thread_id).replace("agent-bus:", ""),
                after_turn=after_turn,
                from_agent=str(parsed.get("from_agent") or "web-anthropic"),
                model=str(parsed["model"]) if parsed.get("model") else None,
                fired_at=str(parsed.get("fired_at") or ""),
            )
        )
    return out


def received_execution_ids(
    dispatch_id: str, *, spool_dir: Path | str | None = None
) -> set[str]:
    """Execution ids the bridge saw returned to the agent (wait/get/fetch)."""
    root = Path(spool_dir) if spool_dir is not None else steer_spool_dir()
    path = _ledger_path(root, dispatch_id)
    if not path.is_file():
        return set()
    out: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(parsed, dict):
            continue
        if str(parsed.get("kind") or "") != "received":
            continue
        execution_id = parsed.get("execution_id")
        if isinstance(execution_id, str) and execution_id:
            out.add(execution_id)
    return out


def outstanding_generates(
    dispatch_id: str, *, spool_dir: Path | str | None = None
) -> list[AwaitedGenerate]:
    """Fired generates the agent has not yet received (R1)."""
    received = received_execution_ids(dispatch_id, spool_dir=spool_dir)
    return [
        g
        for g in fired_generates(dispatch_id, spool_dir=spool_dir)
        if g.execution_id not in received
    ]


def find_reply(
    awaited: AwaitedGenerate, turns: list[dict[str, Any]]
) -> ReplyHit | None:
    """First web-anthropic CDP reply/fail/unverified turn naming *awaited*."""
    for turn in turns:
        if str(turn.get("from") or "") != awaited.from_agent:
            continue
        try:
            turn_number = int(turn["turn_number"])
        except (KeyError, TypeError, ValueError):
            continue
        if turn_number <= awaited.after_turn:
            continue
        subject = str(turn.get("subject") or "")
        body = str(turn.get("body") or "")
        if awaited.execution_id not in body and awaited.execution_id[:8] not in subject:
            continue
        match = _REPLY_SUBJECT_RE.match(subject.strip())
        if match is None:
            # Body carries execution_id; accept as reply when subject is loose.
            if awaited.execution_id not in body:
                continue
            outcome = "reply"
        else:
            token = match.group(1).lower()
            outcome = _OUTCOME_BY_TOKEN.get(token, "reply")
        return ReplyHit(
            execution_id=awaited.execution_id,
            thread_id=awaited.thread_id,
            turn_number=turn_number,
            outcome=outcome,
            body=body,
            subject=subject,
        )
    return None


def _default_bus_turns(thread_id: str, after_turn: int) -> list[dict[str, Any]]:
    """Sync GET /turns with after_turn cursor; raise on transport failure.

    Agent-bus ``last`` defaults to None (no LIMIT). Passing ``after_turn`` uses
    the server's ASC page filter so a tip-window ``last`` cannot hide a reply.
    """
    from transport_utils import DEFAULT_AGENT_BUS_URL, make_sync_client

    token = os.environ.get("AGENT_BUS_TOKEN", "").strip()
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    with make_sync_client(DEFAULT_AGENT_BUS_URL, timeout=5.0) as client:
        resp = client.get(
            "/turns",
            params={"thread": thread_id, "after_turn": after_turn},
            headers=headers,
        )
    if resp.status_code >= 400:
        raise ConnectionError(f"bus turns HTTP {resp.status_code}")
    payload = resp.json()
    if not isinstance(payload, dict):
        raise ConnectionError("bus turns payload not a dict")
    turns = payload.get("turns")
    if not isinstance(turns, list):
        raise ConnectionError("bus turns missing turns list")
    # Client-side backstop if the server ignores after_turn (1e).
    return [
        t
        for t in turns
        if isinstance(t, dict) and int(t.get("turn_number") or 0) > after_turn
    ]


def _is_conductor_dispatch(dispatch_id: str) -> bool:
    from services.git_integration_worker.cursor_sdk_conductor_identity import (
        is_conductor_dispatch_row,
    )

    ledger = CursorDispatchLedger.instance()
    with ledger._connect() as conn:
        row = conn.execute(
            "SELECT contract FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            (dispatch_id,),
        ).fetchone()
    if row is None:
        return False
    return is_conductor_dispatch_row({"contract": row["contract"]})


async def _snapshot_replies(
    awaited: list[AwaitedGenerate],
    *,
    bus_turns_fn: BusTurnsFn,
) -> tuple[dict[str, ReplyHit | None], bool]:
    """Map execution_id → reply (or None). Second value True when a fetch failed."""
    hits: dict[str, ReplyHit | None] = {}
    fetch_failed = False
    by_thread: dict[tuple[str, int], list[dict[str, Any]] | None] = {}
    for gen in awaited:
        key = (gen.thread_id, gen.after_turn)
        if key not in by_thread:
            try:
                by_thread[key] = await asyncio.to_thread(
                    bus_turns_fn, gen.thread_id, gen.after_turn
                )
            except Exception:  # noqa: BLE001 — park rather than drop
                by_thread[key] = None
                fetch_failed = True
        turns = by_thread[key]
        if turns is None:
            hits[gen.execution_id] = None
            continue
        hits[gen.execution_id] = find_reply(gen, turns)
    return hits, fetch_failed


def mark_await_parked(
    *,
    dispatch_id: str,
    awaited: list[AwaitedGenerate],
    ttl_s: int | None = None,
) -> ParkRow | None:
    """Stamp await park columns + ``record_json.park``; status untouched (D1).

    Refuses to overwrite a lineage that already has ``park_resumed_by`` set (F8).
    """
    ttl = await_reply_ttl_s() if ttl_s is None else ttl_s
    parked_dt = _now_dt()
    parked_at = parked_dt.isoformat()
    expires_at = (parked_dt + timedelta(seconds=ttl)).isoformat()
    ledger = CursorDispatchLedger.instance()
    with ledger._connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT record_json FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            (dispatch_id,),
        ).fetchone()
        if row is None:
            return None
        data = _record_data(row["record_json"])
        data["resume_retain"] = True
        data[_RECORD_KEY] = {
            "kind": PARK_KIND_AWAIT_REPLY,
            "parked_at": parked_at,
            "ttl_s": ttl,
            "awaited": [
                {
                    "execution_id": g.execution_id,
                    "thread_id": g.thread_id,
                    "after_turn": g.after_turn,
                    "from_agent": g.from_agent,
                    "model": g.model,
                    "fired_at": g.fired_at,
                }
                for g in awaited
            ],
            "resume_attempts": 0,
            "resume_refusals": [],
        }
        cur = conn.execute(
            "UPDATE cursor_sdk_dispatches SET park_kind=?, parked_at=?, "
            "park_expires_at=?, park_resumed_by=NULL, record_json=? "
            "WHERE dispatch_id=? AND (park_kind IS NULL OR "
            "(park_kind=? AND park_resumed_by IS NULL))",
            (
                PARK_KIND_AWAIT_REPLY,
                parked_at,
                expires_at,
                _dump(data),
                dispatch_id,
                PARK_KIND_AWAIT_REPLY,
            ),
        )
        if cur.rowcount == 0:
            return None
    return load_park_row(dispatch_id=dispatch_id)


def seal_running_await_on_boot(dispatch_id: str) -> bool:
    """Terminal-seal a restart orphan that already carries ``await_cdp_reply``.

    ``mark_await_parked`` stamps park columns before the terminal mark. A
    crash in that window leaves ``status=running``. Boot rewire would
    overwrite that row as ``park_for_restart`` and resume without the CDP
    reply. Preserve the await park and seal ``cancelled`` so
    ``open_await_rows`` can see it. Returns True when this call sealed.
    """
    ledger = CursorDispatchLedger.instance()
    with ledger._connect() as conn:
        row = conn.execute(
            "SELECT status, park_kind FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            (dispatch_id,),
        ).fetchone()
    if row is None:
        return False
    if row["status"] != "running" or row["park_kind"] != PARK_KIND_AWAIT_REPLY:
        return False
    ledger.mark_terminal(dispatch_id=dispatch_id, terminal_status="cancelled")
    return True


def open_await_rows() -> list[ParkRow]:
    """Open ``await_cdp_reply`` rows, oldest first (includes TTL-expired).

    Terminal statuses only — a crash between park and terminal mark is recovered
    by the stale-lease reaper, not by admitting while the parent is still running
    (friction 34156 F4). Also reclaims parks whose hand-stamped child failed
    before ``started_at`` was set (delta-review 1b).
    """
    ledger = CursorDispatchLedger.instance()
    with ledger._connect() as conn:
        # Clear orphan stamps before selecting open rows.
        orphans = conn.execute(
            "SELECT p.dispatch_id AS parent_id, p.park_resumed_by AS child_id "
            "FROM cursor_sdk_dispatches p "
            "JOIN cursor_sdk_dispatches c ON c.dispatch_id = p.park_resumed_by "
            "WHERE p.park_kind=? AND p.park_resumed_by IS NOT NULL "
            "AND p.status IN ('completed','failed','cancelled') "
            "AND c.status='failed' AND c.started_at IS NULL",
            (PARK_KIND_AWAIT_REPLY,),
        ).fetchall()
        for orphan in orphans:
            conn.execute(
                "UPDATE cursor_sdk_dispatches SET park_resumed_by=NULL "
                "WHERE dispatch_id=? AND park_resumed_by=?",
                (orphan["parent_id"], orphan["child_id"]),
            )
        rows = conn.execute(
            "SELECT dispatch_id, thread_id, execution_id, caller_agent, "
            "resolved_model, status, terminal_status, sdk_agent_id, state_root, "
            "source_ref, work_key, contract, packet_path, park_kind, "
            "park_intent_id, parked_at, park_resumed_by, park_expires_at, "
            "record_json FROM cursor_sdk_dispatches "
            "WHERE park_kind=? AND park_resumed_by IS NULL "
            "AND status IN ('completed','failed','cancelled') "
            "ORDER BY parked_at ASC, rowid ASC",
            (PARK_KIND_AWAIT_REPLY,),
        ).fetchall()
    return [ParkRow(**{k: r[k] for k in r.keys()}) for r in rows]


def _awaited_from_row(row: ParkRow) -> list[AwaitedGenerate]:
    park = row.park
    raw = park.get("awaited") if isinstance(park.get("awaited"), list) else []
    out: list[AwaitedGenerate] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        execution_id = item.get("execution_id")
        thread_id = item.get("thread_id")
        if not isinstance(execution_id, str) or not isinstance(thread_id, str):
            continue
        try:
            after_turn = int(item.get("after_turn", 0) or 0)
        except (TypeError, ValueError):
            after_turn = 0
        out.append(
            AwaitedGenerate(
                execution_id=execution_id,
                thread_id=thread_id,
                after_turn=after_turn,
                from_agent=str(item.get("from_agent") or "web-anthropic"),
                model=str(item["model"]) if item.get("model") else None,
                fired_at=str(item.get("fired_at") or ""),
            )
        )
    return out


def child_dispatch_id(parent_id: str, *, attempt: int) -> str:
    return f"{parent_id}-c{attempt}"


def _cap_body(body: str, *, limit: int = 4000) -> str:
    text = body.strip()
    if len(text) <= limit:
        return text
    return text[: limit - 20] + "\n…(truncated)…"


def render_await_resume_preamble(
    row: ParkRow,
    *,
    replies: list[ReplyHit | None],
    awaited: list[AwaitedGenerate],
    code_version: str,
    ttl_s: int,
    bus_unreachable: bool = False,
) -> str:
    """CDP-REPLY-RESUME v1 notice with every awaited reply / timeout line."""
    lines = [
        f"CDP-REPLY-RESUME v{AWAIT_REPLY_PREAMBLE_VERSION} — substrate notice, "
        "not operator prose.",
        f"You are the same agent as dispatch {row.dispatch_id} on "
        f"agent-bus:{row.thread_id}. GIW parked you for outstanding CDP replies; "
        f"the substrate is back at code_version {code_version}.",
        "Continue from your own CHECKPOINT/journal; do not redo completed writes; "
        "do not restart the task.",
        "Awaited CDP replies follow. Treat a (timeout) line as the generate "
        "failed and proceed with what you have.",
    ]
    total = len(awaited)
    for index, gen in enumerate(awaited, start=1):
        hit = replies[index - 1] if index - 1 < len(replies) else None
        header = f"Awaited reply {index}/{total} — execution_id {gen.execution_id}"
        if hit is None:
            if bus_unreachable:
                lines.append(
                    f"{header} (unknown) — bus unreachable when the await TTL "
                    f"elapsed on agent-bus:{gen.thread_id}; treat the generate "
                    "as failed."
                )
            else:
                lines.append(
                    f"{header} (timeout) — no reply within {ttl_s}s on "
                    f"agent-bus:{gen.thread_id}; treat the generate as failed."
                )
            continue
        ref = f"agent-bus:{hit.thread_id}#{hit.turn_number}"
        lines.append(f"{header} ({hit.outcome}) — {ref}")
        lines.append(_cap_body(hit.body))
    return "\n".join(lines)


def build_await_resume_request(
    row: ParkRow,
    *,
    replies: list[ReplyHit | None],
    attempt: int,
    code_version: str,
    ttl_s: int | None = None,
    bus_unreachable: bool = False,
) -> CursorDispatchRequest:
    """Mint the ``resume_of`` child carrying every awaited reply body."""
    awaited = _awaited_from_row(row)
    ttl = await_reply_ttl_s() if ttl_s is None else ttl_s
    record = row.record
    contract = str(record.get("handoff_contract") or row.contract or "").lower() or None
    original_preamble = str(record.get("prompt_preamble") or "").strip()
    if not original_preamble and contract == "implement":
        original_preamble = _IMPLEMENT_PREAMBLE
    notice = render_await_resume_preamble(
        row,
        replies=replies,
        awaited=awaited,
        code_version=code_version,
        ttl_s=ttl,
        bus_unreachable=bus_unreachable,
    )
    preamble = (
        f"{notice}\n\n{original_preamble}".strip() if original_preamble else notice
    )
    lane = record.get("lane")
    worktree_path = record.get("worktree_path")
    worktree_isolated = bool(record.get("worktree_isolated", False))
    if lane == "A":
        worktree_path = None
        worktree_isolated = False
    return CursorDispatchRequest(
        thread_id=row.thread_id,
        model=str(record.get("model") or row.resolved_model),
        dispatch_id=child_dispatch_id(row.dispatch_id, attempt=attempt),
        execution_id=row.execution_id or row.dispatch_id,
        caller_agent=row.caller_agent,
        packet_path=record.get("packet_path") or row.packet_path,
        message=record.get("message"),
        handoff_contract=contract,
        prompt_preamble=preamble,
        skills=record.get("skills"),
        model_knobs=record.get("model_knobs"),
        read_only=bool(record.get("read_only", False)),
        lane=lane if lane in ("A", "B") else None,
        worktree_isolated=worktree_isolated,
        worktree_path=worktree_path,
        admitted_via=ADMITTED_VIA_AWAIT_RESUME,
        work_key=row.work_key,
        source_ref=row.source_ref,
        resume_of=row.dispatch_id,
    )


async def _post_awaiting_turn(
    bus: CursorBusClient,
    *,
    dispatch_id: str,
    thread_id: str,
    caller_agent: str | None,
    awaited: list[AwaitedGenerate],
) -> None:
    exec_ids = ", ".join(g.execution_id for g in awaited)
    threads = ", ".join(sorted({g.thread_id for g in awaited}))
    await bus.reply(
        thread_id=thread_id,
        to_agent=caller_agent or "dispatch",
        from_agent="cursor-sdk",
        subject=f"cursor-sdk dispatch {dispatch_id} AWAITING CDP REPLY",
        body=(
            f"Parked for outstanding CDP generate(s): {exec_ids}.\n"
            f"Watching agent-bus thread(s): {threads}.\n"
            "GIW will admit a resume_of child once every awaited reply lands, "
            "fails, or the await TTL elapses."
        ),
    )


async def maybe_await_park_at_terminal(
    *,
    dispatch_id: str,
    thread_id: str,
    execution_id: str | None,
    bus: CursorBusClient,
    spool_dir: Path | str | None = None,
    bus_turns_fn: BusTurnsFn | None = None,
) -> bool:
    """Stamp await-park columns when outstanding CDP generates remain.

    Never raises into the closeout path (F1). Returns True only after
    ``mark_await_parked`` commits. The AWAITING post and parked event run
    *after* ``_mark_terminal_and_promote`` via ``announce_await_parked`` (1c).
    """
    del execution_id  # reserved for future link-aware logging
    del bus  # announce after terminal mark
    del bus_turns_fn  # park no longer snapshots; reactor observes
    del thread_id  # announce uses the row's thread
    try:
        if not await_reply_enabled():
            return False
        # Cheap filesystem check before the conductor DB read (F9).
        outstanding = outstanding_generates(dispatch_id, spool_dir=spool_dir)
        if not outstanding:
            return False
        if _is_conductor_dispatch(dispatch_id):
            return False
        row = mark_await_parked(dispatch_id=dispatch_id, awaited=outstanding)
        if row is None:
            return False
        logger.info(
            "cursor-sdk await_cdp_reply parked dispatch=%s awaited=%s ttl_s=%s",
            dispatch_id,
            [g.execution_id for g in outstanding],
            await_reply_ttl_s(),
        )
        return True
    except Exception:  # noqa: BLE001 — never block _deliver_sdk_closeout (F1)
        logger.exception(
            "await_cdp_reply park hook failed open dispatch=%s", dispatch_id
        )
        return False


async def announce_await_parked(
    *,
    dispatch_id: str,
    thread_id: str,
    bus: CursorBusClient,
) -> None:
    """Best-effort AWAITING post + parked event after the terminal mark (1c)."""
    row = load_park_row(dispatch_id=dispatch_id)
    if row is None or row.park_kind != PARK_KIND_AWAIT_REPLY:
        return
    awaited = _awaited_from_row(row)
    if not awaited:
        return
    ttl = await_reply_ttl_s()
    try:
        emit_sdk_await_reply_parked(
            dispatch_id=dispatch_id,
            thread_id=thread_id,
            execution_ids=[g.execution_id for g in awaited],
            awaited_threads=sorted({g.thread_id for g in awaited}),
            ttl_s=ttl,
        )
    except Exception:  # noqa: BLE001 — best-effort
        logger.exception("await_cdp_reply parked event failed dispatch=%s", dispatch_id)
    try:
        await _post_awaiting_turn(
            bus,
            dispatch_id=dispatch_id,
            thread_id=thread_id,
            caller_agent=row.caller_agent,
            awaited=awaited,
        )
    except Exception:  # noqa: BLE001 — best-effort
        logger.exception(
            "await_cdp_reply AWAITING post failed dispatch=%s", dispatch_id
        )


def _response_code(response: Any) -> str:
    try:
        body = json.loads(bytes(response.body).decode() or "{}")
    except (ValueError, TypeError, AttributeError):
        return f"http_{getattr(response, 'status_code', '?')}"
    if isinstance(body, dict):
        code = body.get("code") or body.get("status")
        if code:
            return str(code)
    return f"http_{getattr(response, 'status_code', '?')}"


def _ttl_elapsed(row: ParkRow, *, now: datetime) -> bool:
    if row.park_expires_at:
        try:
            expires = datetime.fromisoformat(row.park_expires_at)
        except ValueError:
            expires = None
        if expires is not None and now >= expires:
            return True
    return bool(row.park.get("expired_at"))


def _refusal_cap_elapsed(row: ParkRow, *, now: datetime) -> bool:
    """True when resume attempts or TTL+grace say stop retrying refusals (F6)."""
    attempts = int(row.park.get("resume_attempts") or 0)
    if attempts >= _MAX_RESUME_ATTEMPTS:
        return True
    if not row.park_expires_at:
        return False
    try:
        expires = datetime.fromisoformat(row.park_expires_at)
    except ValueError:
        return False
    return now >= (expires + timedelta(seconds=_REFUSAL_GRACE_S))


def _usable_resume_child(parent_id: str) -> str | None:
    """Live, completed, or started-then-terminal child; ignores failed-before-run.

    A child that reached ``started_at`` and then failed/cancelled reconciles so
    we do not mint a second resume of work that already ran (1d).
    """
    ledger = CursorDispatchLedger.instance()
    with ledger._connect() as conn:
        row = conn.execute(
            "SELECT dispatch_id FROM cursor_sdk_dispatches WHERE resume_of=? "
            f"AND (status IN {RECONCILE_CHILD_STATUSES_SQL} "
            "OR (status IN ('failed','cancelled') AND started_at IS NOT NULL)) "
            "ORDER BY rowid LIMIT 1",
            (parent_id,),
        ).fetchone()
    return str(row["dispatch_id"]) if row is not None else None


async def _post_resumed_turn(
    bus: CursorBusClient, *, row: ParkRow, child_id: str, attempt: int
) -> None:
    payload = {
        "status": "resumed",
        "resume_of": row.dispatch_id,
        "dispatch_id": child_id,
        "execution_id": row.execution_id or row.dispatch_id,
        "attempt": attempt,
        "admitted_via": ADMITTED_VIA_AWAIT_RESUME,
        "park_kind": PARK_KIND_AWAIT_REPLY,
    }
    result = await bus.reply(
        thread_id=row.thread_id,
        to_agent=row.caller_agent or "dispatch",
        from_agent="cursor-sdk",
        subject=(
            f"cursor-sdk dispatch {child_id} RESUMED "
            f"(resume_of {row.dispatch_id}, await_cdp_reply)"
        ),
        body=f"```json\n{json.dumps(payload, indent=2, sort_keys=True)}\n```",
    )
    if result.status_code >= 400:
        logger.error(
            "cursor-sdk await RESUMED turn failed child=%s status=%s body=%s",
            child_id,
            result.status_code,
            result.body,
        )


async def _expire_await(row: ParkRow, bus: CursorBusClient, *, reason: str) -> bool:
    """Stamp expiry once; terminate link; awareness turn. False if already done."""
    if not mark_park_expired(parent_id=row.dispatch_id):
        return False
    emit_sdk_await_reply_expired(
        parent_dispatch_id=row.dispatch_id,
        reason=reason,
        parked_at=row.parked_at,
    )
    await bus.terminate_dispatch(
        thread_id=row.thread_id,
        terminal_status=row.terminal_status or row.status or "completed",
        execution_id=row.execution_id or row.dispatch_id,
    )
    await bus.reply(
        thread_id=row.thread_id,
        to_agent=row.caller_agent or "dispatch",
        from_agent="cursor-sdk",
        subject=(
            f"cursor-sdk dispatch {row.dispatch_id} AWAIT EXPIRED "
            f"(no resume child; {reason})"
        ),
        body=(
            f"Await-cdp-reply park for {row.dispatch_id} cannot mint a resume "
            f"child ({reason}). Bus link closed."
        ),
    )
    return True


async def resume_await_parked_dispatches(
    *,
    cfg: WorkerConfig,
    controller: Any,
    code_version: str,
    bus: CursorBusClient | None = None,
    bus_turns_fn: BusTurnsFn | None = None,
    now: datetime | None = None,
    parent_dispatch_id: str | None = None,
) -> AwaitResumeSummary:
    """Admit one resume child per ready await-park row; idempotent on stamp.

    When *parent_dispatch_id* is set (closeout immediate pass), only that row
    is considered so other open parks keep their own code_version (F7).
    """
    from services.git_integration_worker.routes.cursor_sdk import admit_cursor_dispatch

    summary = AwaitResumeSummary()
    bus = bus or CursorBusClient()
    turns_fn = bus_turns_fn or _default_bus_turns
    clock = now or _now_dt()
    ledger = CursorDispatchLedger.instance()
    ttl = await_reply_ttl_s()

    for row in open_await_rows():
        if parent_dispatch_id is not None and row.dispatch_id != parent_dispatch_id:
            continue
        if controller.is_draining():
            break
        if row.park.get("expired_at"):
            continue
        if _is_conductor_dispatch(row.dispatch_id):
            summary.skipped_conductor.append(row.dispatch_id)
            continue
        # Live or completed resume_of child closes the park; failed-before-run
        # children (drain 503 after ledger.admit) do not (F2).
        existing = _usable_resume_child(row.dispatch_id)
        if existing is not None:
            mark_park_resumed(parent_id=row.dispatch_id, child_id=existing)
            summary.reconciled.append((row.dispatch_id, existing))
            continue

        awaited = _awaited_from_row(row)
        hits, fetch_failed = await _snapshot_replies(awaited, bus_turns_fn=turns_fn)
        reply_list = [hits.get(g.execution_id) for g in awaited]
        all_landed = bool(awaited) and all(h is not None for h in reply_list)
        timed_out = _ttl_elapsed(row, now=clock)
        if not all_landed and not timed_out:
            if fetch_failed:
                logger.info(
                    "cursor-sdk await_reply waiting bus-down parent=%s",
                    row.dispatch_id,
                )
            summary.waiting.append(row.dispatch_id)
            continue

        reason = resume_eligibility_reason(ledger, parent_id=row.dispatch_id)
        if reason is not None:
            if reason in _PERMANENT_INELIGIBLE:
                if await _expire_await(row, bus, reason=reason):
                    summary.expired.append(row.dispatch_id)
            elif _refusal_cap_elapsed(row, now=clock):
                if await _expire_await(row, bus, reason=f"resume_refused_cap:{reason}"):
                    summary.expired.append(row.dispatch_id)
            else:
                attempt = bump_resume_attempt(parent_id=row.dispatch_id)
                record_resume_refusal(parent_id=row.dispatch_id, reason=reason)
                emit_sdk_await_reply_resume_refused(
                    parent_dispatch_id=row.dispatch_id,
                    reason=reason,
                    attempt=attempt,
                )
                summary.refused.append((row.dispatch_id, reason))
            continue

        attempt = bump_resume_attempt(parent_id=row.dispatch_id)
        req = build_await_resume_request(
            row,
            replies=reply_list,
            attempt=attempt,
            code_version=code_version,
            ttl_s=ttl,
            bus_unreachable=bool(fetch_failed and not all_landed),
        )
        response = await admit_cursor_dispatch(req, cfg=cfg, controller=controller)
        if response.status_code not in (200, 202):
            code = _response_code(response)
            record_resume_refusal(parent_id=row.dispatch_id, reason=code)
            emit_sdk_await_reply_resume_refused(
                parent_dispatch_id=row.dispatch_id, reason=code, attempt=attempt
            )
            summary.refused.append((row.dispatch_id, code))
            logger.warning(
                "cursor-sdk await resume refused parent=%s child=%s code=%s",
                row.dispatch_id,
                req.dispatch_id,
                code,
            )
            # Reload park for attempt count after bump; expire when capped (F6).
            refreshed = load_park_row(dispatch_id=row.dispatch_id)
            if refreshed is not None and _refusal_cap_elapsed(refreshed, now=clock):
                if await _expire_await(
                    refreshed, bus, reason=f"resume_refused_cap:{code}"
                ):
                    summary.expired.append(row.dispatch_id)
            continue
        mark_park_resumed(parent_id=row.dispatch_id, child_id=req.dispatch_id)
        outcomes = {
            g.execution_id: (
                hits[g.execution_id].outcome
                if hits.get(g.execution_id)
                else ("unknown" if fetch_failed else "timeout")
            )
            for g in awaited
        }
        emit_sdk_await_reply_resume_admitted(
            parent_dispatch_id=row.dispatch_id,
            child_dispatch_id=req.dispatch_id,
            thread_id=row.thread_id,
            outcomes=outcomes,
            code_version=code_version,
            attempt=attempt,
        )
        await _post_resumed_turn(
            bus, row=row, child_id=req.dispatch_id, attempt=attempt
        )
        summary.admitted.append((row.dispatch_id, req.dispatch_id))
        logger.info(
            "cursor-sdk await resumed parent=%s child=%s code_version=%s",
            row.dispatch_id,
            req.dispatch_id,
            code_version,
        )
    return summary


__all__ = [
    "ADMITTED_VIA_AWAIT_RESUME",
    "AWAIT_REPLY_FLAG_ENV",
    "AWAIT_REPLY_PREAMBLE_VERSION",
    "AWAIT_REPLY_TTL_ENV",
    "PARK_KIND_AWAIT_REPLY",
    "AwaitedGenerate",
    "AwaitResumeSummary",
    "ReplyHit",
    "await_reply_enabled",
    "await_reply_ttl_s",
    "build_await_resume_request",
    "child_dispatch_id",
    "find_reply",
    "fired_generates",
    "announce_await_parked",
    "mark_await_parked",
    "maybe_await_park_at_terminal",
    "open_await_rows",
    "seal_running_await_on_boot",
    "outstanding_generates",
    "received_execution_ids",
    "resume_await_parked_dispatches",
]
