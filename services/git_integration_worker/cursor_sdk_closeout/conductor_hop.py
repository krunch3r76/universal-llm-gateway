"""Conductor row-hop reactor (todo:conductor-hop-reactor R3).

Fires after ``ledger.mark_terminal`` on the closeout hot path. Closeout authority
(``hop_declared``, stop tokens) is merged **before** terminal via
``merge_conductor_closeout_hop_authority``.
"""

from __future__ import annotations

import json
import re
from typing import Any

import httpx
from claude_bundles.conductor_stop import (
    EXIT_PERSIST_STOPS,
    parse_stop_tokens,
)
from transport_utils import DEFAULT_STARGATE_URL, make_async_client
from universal_logging import get_logger

from services.git_integration_worker.cursor_dispatch_ledger import (
    CursorDispatchLedger,
)
from services.git_integration_worker.cursor_sdk_closeout.conductor_exit_reasons import (
    conductor_has_live_nested,
)
from services.git_integration_worker.cursor_sdk_closeout.conductor_hop_budget import (
    budget_ok_for_hop,
    build_budget_authority_patch,
    evaluate_hop_budget,
    park_conductor_hop_mission,
)
from services.git_integration_worker.cursor_sdk_conductor_conflict import (
    _record_packet_kind,
)
from services.git_integration_worker.cursor_sdk_hop_events import (
    emit_frontier_sdk_conductor_hop_admit_failed,
    emit_frontier_sdk_conductor_hop_admitted,
    emit_frontier_sdk_conductor_hop_declared,
    emit_frontier_sdk_conductor_hop_skipped,
)
from services.git_integration_worker.cursor_sdk_ledger_hop import (
    hop_fields_from_record_json,
    merge_hop_patch,
)

logger = get_logger(__name__)

_LIVE_STATUSES = frozenset(
    {"queued", "admitted", "running", "parked_waiting"}
)
_CLOSEOUT_TOKENS_KEY = "closeout_stop_tokens"
_HOP_SEQ_LINE_RE = re.compile(
    r"(?im)^(?:\*\*)?hop_seq(?:\*\*)?:\s*(\d+)\s*$"
)
_RELAY_TIMEOUT = httpx.Timeout(connect=5.0, read=30.0, write=15.0, pool=5.0)


def _load_row(dispatch_id: str) -> dict[str, Any] | None:
    ledger = CursorDispatchLedger.instance()
    with ledger._connect() as conn:
        row = conn.execute(
            "SELECT * FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            (dispatch_id,),
        ).fetchone()
    if row is None:
        return None
    return {k: row[k] for k in row.keys()}


def _is_conductor_row(row: dict[str, Any]) -> bool:
    record_json = str(row.get("record_json") or "")
    return _record_packet_kind(record_json) == "conductor"


def _record_data(row: dict[str, Any]) -> dict[str, Any]:
    record_json = str(row.get("record_json") or "")
    try:
        data = json.loads(record_json) if record_json else {}
    except json.JSONDecodeError:
        data = {}
    return data if isinstance(data, dict) else {}


def _resolve_source_ref(row: dict[str, Any], rec: dict[str, Any]) -> str:
    for candidate in (
        rec.get("source_ref"),
        row.get("source_ref"),
        row.get("work_key"),
    ):
        text = str(candidate or "").strip()
        if text:
            return text
    return ""


def _successor_hop_seq(row: dict[str, Any], rec: dict[str, Any]) -> int:
    """Closeout ``hop_seq`` is this row's seq; successor is +1."""
    closeout_seq = rec.get("closeout_hop_seq")
    if isinstance(closeout_seq, int):
        return closeout_seq + 1
    hop_fields = hop_fields_from_record_json(str(row.get("record_json") or ""))
    prior_seq = hop_fields.get("hop_seq")
    if isinstance(prior_seq, int):
        return prior_seq + 1
    return 1


def _hop_skip_gate(
    row: dict[str, Any],
    *,
    closeout_tokens: frozenset[str],
) -> str | None:
    """Return skip gate name when hop reactor must not POST successor."""
    if not hop_owed(row, closeout_tokens=closeout_tokens):
        status = str(row.get("status") or "")
        if status not in ("completed", "failed", "cancelled"):
            return "mission_closed"
        tokens = closeout_tokens
        if tokens & (EXIT_PERSIST_STOPS | frozenset({"DONE"})):
            return "mission_closed"
        thread_id = str(row.get("thread_id") or "")
        dispatch_id = str(row.get("dispatch_id") or "")
        if thread_id and dispatch_id and live_conductor_row_on_thread(
            thread_id=thread_id, exclude_dispatch_id=dispatch_id
        ):
            return "live_sibling"
        if dispatch_id and conductor_has_live_nested(dispatch_id=dispatch_id):
            return "live_nested"
        if not mission_open_for_row(row, closeout_tokens=tokens):
            return "mission_closed"
        if not budget_ok_for_hop(row, closeout_tokens=tokens):
            return "budget"
        hop_fields = hop_fields_from_record_json(str(row.get("record_json") or ""))
        if hop_fields.get("hop_successor"):
            return "already_hopped"
        return "mission_closed"
    return None


def _emit_hop_skipped(
    row: dict[str, Any],
    *,
    gate: str,
    hop_seq: int | None = None,
) -> None:
    dispatch_id = str(row.get("dispatch_id") or "")
    thread_id = str(row.get("thread_id") or "")
    if not dispatch_id or not thread_id:
        return
    if hop_seq is None:
        rec = _record_data(row)
        hop_seq = _successor_hop_seq(row, rec) - 1
        if hop_seq < 1:
            hop_seq = 1
    emit_frontier_sdk_conductor_hop_skipped(
        dispatch_id=dispatch_id,
        thread_id=thread_id,
        hop_seq=int(hop_seq),
        gate=gate,
    )


def _closeout_tokens_from_row(row: dict[str, Any]) -> frozenset[str]:
    record_json = str(row.get("record_json") or "")
    try:
        data = json.loads(record_json) if record_json else {}
    except json.JSONDecodeError:
        data = {}
    if not isinstance(data, dict):
        return frozenset()
    raw = data.get(_CLOSEOUT_TOKENS_KEY)
    if isinstance(raw, list):
        return frozenset(str(t).upper() for t in raw)
    return frozenset()


def _parse_hop_seq_from_closeout(body: str) -> int | None:
    match = _HOP_SEQ_LINE_RE.search(body or "")
    if match is None:
        return None
    return int(match.group(1))


def _infer_hop_reason(
    *,
    closeout_tokens: frozenset[str],
    terminal_status: str,
) -> str:
    if "ROW_HOP" in closeout_tokens:
        return "planned"
    if terminal_status == "failed":
        return "crash"
    return "silent"


def live_conductor_row_on_thread(
    *,
    thread_id: str,
    exclude_dispatch_id: str | None = None,
) -> bool:
    """True when another live conductor row holds ``thread_id``."""
    ledger = CursorDispatchLedger.instance()
    with ledger._connect() as conn:
        rows = conn.execute(
            "SELECT dispatch_id, record_json, contract, work_key "
            "FROM cursor_sdk_dispatches "
            "WHERE thread_id=? AND status IN ('queued','admitted','running','parked_waiting') "
            "AND dispatch_id<>?",
            (thread_id, exclude_dispatch_id or ""),
        ).fetchall()
    return any(_is_conductor_row({k: row[k] for k in row.keys()}) for row in rows)


def mission_open_for_row(
    row: dict[str, Any],
    *,
    closeout_tokens: frozenset[str],
) -> bool:
    """Scoreboard fold still has a non-DONE row (bind §2.6 hop_owed)."""
    if "DONE" in closeout_tokens:
        return False
    work_key = str(row.get("work_key") or "")
    if not work_key.startswith("todo:"):
        return True
    slug = work_key.split(":", 1)[1].strip()
    if not slug:
        return True
    try:
        from implement_admission.conductor_witness import fold_scoreboard
        from implement_admission.conductor_witness_defaults import DefaultWitnessCortex
        from implement_admission.conductor_witness_types import FoldDeps

        fold = fold_scoreboard(
            slug,
            deps=FoldDeps(cortex=DefaultWitnessCortex()),
            write_journal=False,
        )
        if fold is None:
            return True
        return any(status != "DONE" for status in fold.row_status.values())
    except Exception as exc:  # noqa: BLE001 — fold is advisory; token gate remains
        logger.warning(
            "conductor hop mission_open fold failed slug=%s err=%s",
            slug,
            exc,
        )
        return "DONE" not in closeout_tokens


def hop_owed(
    row: dict[str, Any],
    *,
    closeout_tokens: frozenset[str] | None = None,
) -> bool:
    """Predicate from bind §2.6 item 3 (row must already be terminal)."""
    status = str(row.get("status") or "")
    if status not in ("completed", "failed", "cancelled"):
        return False
    tokens = closeout_tokens or _closeout_tokens_from_row(row)
    if tokens & (EXIT_PERSIST_STOPS | frozenset({"DONE"})):
        return False
    thread_id = str(row.get("thread_id") or "")
    dispatch_id = str(row.get("dispatch_id") or "")
    if not thread_id or not dispatch_id:
        return False
    if live_conductor_row_on_thread(
        thread_id=thread_id, exclude_dispatch_id=dispatch_id
    ):
        return False
    if conductor_has_live_nested(dispatch_id=dispatch_id):
        return False
    if not mission_open_for_row(row, closeout_tokens=tokens):
        return False
    if not budget_ok_for_hop(row, closeout_tokens=tokens):
        return False
    hop_fields = hop_fields_from_record_json(str(row.get("record_json") or ""))
    if hop_fields.get("hop_successor"):
        return False
    return True


def _write_record_json(dispatch_id: str, record_json: str) -> None:
    ledger = CursorDispatchLedger.instance()
    with ledger._connect() as conn:
        conn.execute(
            "UPDATE cursor_sdk_dispatches SET record_json=? WHERE dispatch_id=?",
            (record_json, dispatch_id),
        )


def _write_budget_authority(dispatch_id: str, row: dict[str, Any]) -> None:
    ledger = CursorDispatchLedger.instance()
    ledger.merge_record_json(
        dispatch_id=dispatch_id,
        patch=build_budget_authority_patch(row),
    )


def merge_conductor_closeout_hop_authority(
    *,
    dispatch_id: str,
    closeout_body: str,
    thread_id: str,
) -> None:
    """Merge ``hop_declared`` and closeout tokens before ``mark_terminal``."""
    parsed = parse_stop_tokens(closeout_body)
    tokens = parsed.tokens
    row = _load_row(dispatch_id)
    if row is None or not _is_conductor_row(row):
        return
    record_json = str(row.get("record_json") or "")
    try:
        data = json.loads(record_json) if record_json else {}
    except json.JSONDecodeError:
        data = {}
    if not isinstance(data, dict):
        data = {}
    data[_CLOSEOUT_TOKENS_KEY] = sorted(tokens)
    if "ROW_HOP" in tokens:
        data["hop_declared"] = True
        hop_seq = _parse_hop_seq_from_closeout(closeout_body)
        if hop_seq is None:
            prior = hop_fields_from_record_json(str(row.get("record_json") or ""))
            prior_seq = prior.get("hop_seq")
            hop_seq = int(prior_seq) if isinstance(prior_seq, int) else 1
        data["closeout_hop_seq"] = hop_seq
    _write_record_json(
        dispatch_id,
        json.dumps(data, sort_keys=True, separators=(",", ":")),
    )
    row = _load_row(dispatch_id) or row
    if "ROW_HOP" in tokens:
        hop_seq = data.get("closeout_hop_seq")
        if not isinstance(hop_seq, int):
            hop_seq = _parse_hop_seq_from_closeout(closeout_body)
        if hop_seq is None:
            prior = hop_fields_from_record_json(str(row.get("record_json") or ""))
            prior_seq = prior.get("hop_seq")
            hop_seq = int(prior_seq) if isinstance(prior_seq, int) else 1
        emit_frontier_sdk_conductor_hop_declared(
            dispatch_id=dispatch_id,
            thread_id=thread_id,
            hop_seq=hop_seq,
            hop_reason="planned",
        )


def build_conductor_hop_idempotency_key(predecessor_dispatch_id: str) -> str:
    return f"conductor-hop:{predecessor_dispatch_id}"


def build_hop_team_dispatch_body(
    row: dict[str, Any],
    *,
    hop_reason_override: str | None = None,
) -> dict[str, Any] | None:
    """Clone predecessor ledger record for Stargate ``team_dispatch`` generate."""
    if not _is_conductor_row(row):
        return None
    record_json = str(row.get("record_json") or "")
    try:
        rec = json.loads(record_json) if record_json else {}
    except json.JSONDecodeError:
        rec = {}
    if not isinstance(rec, dict):
        rec = {}
    closeout_tokens = _closeout_tokens_from_row(row)
    predecessor_id = str(row.get("dispatch_id") or "")
    thread_id = str(row.get("thread_id") or "")
    source_ref = _resolve_source_ref(row, rec)
    if not thread_id:
        return None
    if not source_ref:
        return None
    next_seq = _successor_hop_seq(row, rec)
    hop_reason = hop_reason_override or _infer_hop_reason(
        closeout_tokens=closeout_tokens,
        terminal_status=str(row.get("terminal_status") or row.get("status") or ""),
    )
    generation_options = dict(rec.get("generation_options") or {})
    summon_mode = generation_options.get("summon_mode")
    if summon_mode is None and rec.get("summon_mode"):
        summon_mode = rec.get("summon_mode")
    if summon_mode is not None:
        generation_options["summon_mode"] = summon_mode
    generation_options["idempotency_key"] = build_conductor_hop_idempotency_key(
        predecessor_id
    )
    summoning_thread_id = str(rec.get("summoning_thread_id") or "").strip()
    dispatch_thread_id = summoning_thread_id or thread_id
    routing_model = rec.get("model") or row.get("resolved_model")
    body: dict[str, Any] = {
        "op": "generate",
        "seat": "cursor-sdk",
        "contract": "light-bounded",
        "caller_agent": "conductor-hop",
        "dispatch_thread_id": dispatch_thread_id,
        "reuse_thread": thread_id,
        "source_ref": source_ref,
        "packet_kind": "conductor",
        "lane": rec.get("lane") or "B",
        "model": routing_model,
        "generation_options": generation_options,
    }
    if rec.get("model_knobs"):
        body["model_knobs"] = rec.get("model_knobs")
    body["hop_from"] = predecessor_id
    body["hop_seq"] = next_seq
    body["hop_reason"] = hop_reason
    return body


async def post_conductor_hop_team_dispatch(
    body: dict[str, Any],
    *,
    stargate_url: str | None = None,
) -> tuple[bool, dict[str, Any]]:
    """POST ``/api/v1/team/dispatch``; return ``(ok, detail)``."""
    base = (stargate_url or DEFAULT_STARGATE_URL).rstrip("/")
    endpoint = "/api/v1/team/dispatch"
    try:
        async with make_async_client(base, timeout=_RELAY_TIMEOUT) as client:
            resp = await client.post(endpoint, json=body)
    except httpx.HTTPError as exc:
        logger.warning("conductor hop team_dispatch transport error: %s", exc)
        return False, {"error": str(exc), "reason": "stargate_unreachable"}
    try:
        payload = resp.json()
    except ValueError:
        payload = {"error": "non_json_response", "text": resp.text[:300]}
    if not isinstance(payload, dict):
        payload = {"error": "non_object_response"}
    if resp.status_code >= 400 or payload.get("error"):
        return False, {
            "status_code": resp.status_code,
            "error": payload,
        }
    return True, payload


async def maybe_fire_conductor_hop_reactor(*, dispatch_id: str) -> None:
    """Evaluate ``hop_owed`` and POST successor admit when due (after terminal)."""
    row = _load_row(dispatch_id)
    if row is None:
        return
    if not _is_conductor_row(row):
        _emit_hop_skipped(row, gate="not_conductor_row")
        return
    closeout_tokens = _closeout_tokens_from_row(row)
    _write_budget_authority(dispatch_id, row)
    row = _load_row(dispatch_id) or row
    verdict = evaluate_hop_budget(row, closeout_tokens=closeout_tokens)
    if verdict.park and verdict.reason:
        await park_conductor_hop_mission(
            row,
            reason=verdict.reason,
        )
        return
    skip_gate = _hop_skip_gate(row, closeout_tokens=closeout_tokens)
    if skip_gate is not None:
        _emit_hop_skipped(row, gate=skip_gate)
        return
    body = build_hop_team_dispatch_body(row)
    if body is None:
        rec = _record_data(row)
        gate = "missing_source_ref" if not _resolve_source_ref(row, rec) else "body_build_failed"
        _emit_hop_skipped(row, gate=gate)
        return
    thread_id = str(row.get("thread_id") or "")
    hop_seq = int(body.get("hop_seq") or 1)
    hop_reason = str(body.get("hop_reason") or "planned")
    ok, detail = await post_conductor_hop_team_dispatch(body)
    record_json = str(row.get("record_json") or "")
    if ok:
        successor = (
            str(detail.get("dispatch_id") or "")
            or str(detail.get("execution_id") or "")
        )
        if successor:
            merged = merge_hop_patch(
                record_json,
                {"hop_successor": successor},
            )
            _write_record_json(dispatch_id, merged)
            emit_frontier_sdk_conductor_hop_admitted(
                predecessor_dispatch_id=dispatch_id,
                successor_dispatch_id=successor,
                thread_id=thread_id,
                hop_seq=hop_seq,
                hop_reason=hop_reason,
            )
        else:
            logger.warning(
                "conductor hop admit ok but no successor id dispatch_id=%s detail=%s",
                dispatch_id,
                detail,
            )
        return
    error_text = json.dumps(detail, sort_keys=True)[:500]
    merged = merge_hop_patch(
        record_json,
        {
            "hop_admit_error": {
                "error": error_text,
                "status_code": detail.get("status_code"),
            }
        },
    )
    _write_record_json(dispatch_id, merged)
    emit_frontier_sdk_conductor_hop_admit_failed(
        dispatch_id=dispatch_id,
        thread_id=thread_id,
        hop_seq=hop_seq,
        hop_reason=hop_reason,
        error=error_text,
        status_code=detail.get("status_code"),
    )


__all__ = [
    "build_conductor_hop_idempotency_key",
    "build_hop_team_dispatch_body",
    "budget_ok_for_hop",
    "hop_owed",
    "live_conductor_row_on_thread",
    "merge_conductor_closeout_hop_authority",
    "maybe_fire_conductor_hop_reactor",
    "mission_open_for_row",
    "post_conductor_hop_team_dispatch",
]
