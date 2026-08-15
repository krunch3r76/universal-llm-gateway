"""Durable consult queue — external fire, backoff, provenance (spec §B row 7)."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from universal_logging import get_logger

from libs.charter_runner_store.db import charter_runner_data_dir, execute_with_retry

from . import bus_client
from .checkpoint_schema import ParsedCheckpoint
from .harvest_attribution import consult_role_from_pickup
from .root_ledger import (
    RootLedgerRow,
    RootStatus,
    Transition,
    load_root,
    upsert_root,
    write_cortex_mirror,
)
from .work_key import compute_work_key

logger = get_logger(__name__)

BACKOFF_SECONDS = (60.0, 300.0, 900.0, 3600.0)
CONSULT_EXHAUST_BUDGET_S = 86400.0
CDP_PRIMARY_MODEL = "cdp/opus-5"


@dataclass(frozen=True)
class ConsultQueueRow:
    root_id: str
    gid: str
    consult_role: str
    corpus_sha: str | None
    attempts: int
    next_retry: float | None
    status: str


@dataclass(frozen=True)
class CdpHarvestResult:
    model_id: str
    consult_thread: str
    harvest_text: str
    escape_path: bool = False


@dataclass(frozen=True)
class ConsultProvenanceRecord:
    consult_thread: str
    verdict: str
    consultant_family: str
    consultant_substrate: str
    consultant_model: str
    evidence_uri: str | None = None


def consult_role_for_row(row: RootLedgerRow) -> str:
    if row.consult_role in {"r_admit", "judgment_gap"}:
        return row.consult_role
    lane = (row.pickup_lane or "judgment").lower()
    return "r_admit" if lane == "consult" else "judgment_gap"


def resolve_consult_role(
    row: RootLedgerRow,
    parsed: ParsedCheckpoint | None,
) -> str:
    """Tip-declared consult role wins over ledger lane inference (a:26872).

    Order: tip ``consult_role`` → Next-pickup sniff → Steps ``[consult:…]`` for
    the open/matching gate → lane default (consult→r_admit). Layer arcs stamp
    ``[consult:judgment_gap]`` on G1/G2 Steps; without the Steps sniff those
    births false-default to ``r_admit`` (dogfood 6489).
    """
    if parsed is not None and parsed.consult_role in {"r_admit", "judgment_gap"}:
        return parsed.consult_role
    if parsed is not None:
        sniffed = consult_role_from_pickup(parsed.next_pickup)
        if sniffed in {"r_admit", "judgment_gap"}:
            return sniffed
        from .gate_lane_classifier import classify, parse_gate_rows

        req = classify(parse_gate_rows(parsed.steps))
        if (
            req is not None
            and req.kind == "consult"
            and req.role in {"r_admit", "judgment_gap"}
        ):
            gid = (row.pickup_gid or "").upper()
            if not gid or req.gate_id is None or req.gate_id.upper() == gid:
                return req.role
    return consult_role_for_row(row)


def backoff_s(attempts: int) -> float:
    idx = min(max(attempts, 1) - 1, len(BACKOFF_SECONDS) - 1)
    return BACKOFF_SECONDS[idx]


# Back-compat aliases for kernel_tick / tests during Phase 3 cutover.
_consult_role_for_row = consult_role_for_row
_backoff_s = backoff_s


def load_queue_row(conn, root_id: str, gid: str, role: str) -> ConsultQueueRow | None:
    row = conn.execute(
        """
        SELECT root_id, gid, consult_role, corpus_sha, attempts, next_retry, status
        FROM consult_queue
        WHERE root_id = ? AND gid = ? AND consult_role = ?
        """,
        (root_id, gid, role),
    ).fetchone()
    if row is None:
        return None
    return ConsultQueueRow(
        root_id=row["root_id"],
        gid=row["gid"],
        consult_role=row["consult_role"],
        corpus_sha=row["corpus_sha"],
        attempts=int(row["attempts"] or 0),
        next_retry=row["next_retry"],
        status=row["status"],
    )


_load_queue_row = load_queue_row


def _parse_env_facts(row: RootLedgerRow) -> dict[str, Any]:
    raw = row.env_facts_json
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _stamp_consult_work_key_env(
    row: RootLedgerRow,
    *,
    consult_role: str,
    source_ref: str | None = None,
) -> str:
    """Persist consult work_key on the ledger for heal lookup (no tip parse at heal)."""
    facts = _parse_env_facts(row)
    work_key = compute_work_key(
        root_id=row.root_id,
        source_ref=source_ref,
        pickup_gid=row.pickup_gid,
        consult_role=consult_role,
        admission_mode="consult",
        pickup_lane=row.pickup_lane,
    )
    facts["consult_work_key"] = work_key
    if source_ref:
        facts["consult_source_ref"] = source_ref
    return json.dumps(facts, sort_keys=True)


def _ledger_row_consult_queued(
    existing: RootLedgerRow,
    *,
    consult_role: str,
    now: float,
    source_ref: str | None = None,
) -> RootLedgerRow:
    next_status = (
        existing.status
        if existing.status in (RootStatus.BLOCKED, RootStatus.CLOSED)
        else RootStatus.CONSULT_QUEUED
    )
    return RootLedgerRow(
        root_id=existing.root_id,
        status=next_status,
        pickup_gid=existing.pickup_gid,
        pickup_lane=existing.pickup_lane,
        pickup_executor=existing.pickup_executor,
        attendance=existing.attendance,
        scoreboard_uri=existing.scoreboard_uri,
        wip_window_id=existing.wip_window_id,
        revise_count=existing.revise_count,
        consult_role=consult_role,
        consult_attempts=existing.consult_attempts,
        consult_next_retry=existing.consult_next_retry,
        consult_poll_from=existing.consult_poll_from,
        harvest_deadline=existing.harvest_deadline,
        last_window_id=existing.last_window_id,
        last_transition=Transition.QUEUE_CONSULT.value,
        last_error=existing.last_error,
        env_facts_json=_stamp_consult_work_key_env(
            existing, consult_role=consult_role, source_ref=source_ref
        ),
        conveyor_phase=existing.conveyor_phase,
        pickup_append_cursor=existing.pickup_append_cursor,
        updated_at=now,
    )


def sync_ledger_consult_queued(
    conn,
    *,
    row: RootLedgerRow,
    consult_role: str,
    source_ref: str | None = None,
) -> RootLedgerRow:
    """Align ledger to ``CONSULT_QUEUED`` when the durable queue already holds a row."""
    now = time.time()
    existing = load_root(conn, row.root_id) or row
    updated = _ledger_row_consult_queued(
        existing, consult_role=consult_role, now=now, source_ref=source_ref
    )
    upsert_root(conn, updated)
    write_cortex_mirror(updated)
    return updated


def enqueue_consult(
    conn,
    *,
    row: RootLedgerRow,
    consult_role: str,
    corpus_sha: str | None = None,
    source_ref: str | None = None,
) -> ConsultQueueRow:
    """Insert/update consult_queue and atomically set ledger ``CONSULT_QUEUED`` (a:26936)."""
    gid = row.pickup_gid or "G?"
    now = time.time()
    execute_with_retry(
        conn,
        """
        INSERT INTO consult_queue
          (root_id, gid, consult_role, corpus_sha, attempts, next_retry, status,
           created_at, updated_at)
        VALUES (?, ?, ?, ?, 0, NULL, 'queued', ?, ?)
        ON CONFLICT(root_id, gid, consult_role) DO UPDATE SET
          status='queued', corpus_sha=excluded.corpus_sha, updated_at=excluded.updated_at
        """,
        (row.root_id, gid, consult_role, corpus_sha, now, now),
    )
    sync_ledger_consult_queued(
        conn, row=row, consult_role=consult_role, source_ref=source_ref
    )
    return load_queue_row(conn, row.root_id, gid, consult_role)  # type: ignore[return-value]


def _provenance_path(root_id: str) -> Path:
    return charter_runner_data_dir() / "consult-provenance" / f"{root_id}.json"


def write_consult_provenance(
    record: ConsultProvenanceRecord,
    *,
    root_id: str,
    source_ref: str | None = None,
) -> str:
    """Persist provenance locally and mirror to cortex (P2-AC2)."""
    payload = {
        "root_id": root_id,
        "consult_thread": record.consult_thread,
        "verdict": record.verdict,
        "consultant_family": record.consultant_family,
        "consultant_substrate": record.consultant_substrate,
        "consultant_model": record.consultant_model,
        "evidence_uri": record.evidence_uri,
        "written_at": time.time(),
    }
    if source_ref:
        payload["source_ref"] = source_ref
    content = json.dumps(payload, indent=2, sort_keys=True)
    path = _provenance_path(root_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    uri = f"cortex://notes/system/threads/charter-consult-provenance/{root_id}.json"
    if _write_consult_provenance_to_shared_root(uri, content):
        return uri
    rel = uri.removeprefix("cortex://")
    mirror = Path.home() / ".local" / "share" / "cortex" / rel
    mirror.parent.mkdir(parents=True, exist_ok=True)
    mirror.write_text(content, encoding="utf-8")
    logger.warning(
        "consult provenance HOME-only mirror for root_id=%s; not emitting cortex://",
        root_id,
    )
    return ""


def _write_consult_provenance_to_shared_root(uri: str, content: str) -> bool:
    from implement_admission.closeout_helpers import cortex_files_root

    rel = uri.removeprefix("cortex://")
    target = cortex_files_root() / rel
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    except OSError:
        return False
    return True


def load_consult_provenance(root_id: str) -> dict[str, Any] | None:
    path = _provenance_path(root_id)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    return data if isinstance(data, dict) else None


def parse_cdp_consult_harvest(
    worker_turns: list[dict[str, Any]],
    *,
    executor: dict[str, Any] | None = None,
    worker_thread: str = "",
    delivery_turns: list[dict[str, Any]] | None = None,
    root_id: str = "",
) -> CdpHarvestResult | None:
    """Parse ``cdp/opus-*`` primary harvest (B8 — not ``project_ask`` escape)."""
    reviewer = str((executor or {}).get("reviewer_model") or "")
    seat_model = str((executor or {}).get("model") or "")
    model_id = reviewer if reviewer.startswith("cdp/") else seat_model
    closeout = bus_client.closeout_turn_from_turns(worker_turns)
    payload: dict[str, Any] = {}
    if closeout is not None:
        try:
            raw = json.loads(str(closeout.get("body") or ""))
            payload = raw if isinstance(raw, dict) else {}
        except (ValueError, TypeError):
            payload = {}
    if not model_id.startswith("cdp/"):
        model_id = str(payload.get("cdp_model") or payload.get("model") or "")
    escape = bool(
        payload.get("project_ask_execution_id")
        or payload.get("escape_path") == "project_ask"
        or (payload.get("transport") == "project_ask")
    )
    if escape or not model_id.startswith("cdp/"):
        if (
            payload.get("project_ask_execution_id")
            or payload.get("transport") == "project_ask"
        ):
            return CdpHarvestResult(
                model_id=model_id or "project_ask",
                consult_thread=worker_thread,
                harvest_text="",
                escape_path=True,
            )
        return None
    harvest_text = _harvest_text_from_turns(
        worker_turns, payload, delivery_turns=delivery_turns
    )
    thread = str(
        payload.get("consult_thread")
        or (f"agent-bus:{root_id}" if root_id else "")
        or worker_thread
        or ""
    )
    if not harvest_text.strip():
        return None
    return CdpHarvestResult(
        model_id=model_id,
        consult_thread=thread,
        harvest_text=harvest_text,
        escape_path=False,
    )


def _harvest_text_from_turns(
    worker_turns: list[dict[str, Any]],
    closeout: dict[str, Any],
    *,
    delivery_turns: list[dict[str, Any]] | None = None,
) -> str:
    for key in ("content_proof_uri", "archive_uri", "harvest_text"):
        val = closeout.get(key)
        if isinstance(val, str) and val.strip():
            return val
    scan_sets: list[list[dict[str, Any]]] = []
    if delivery_turns:
        scan_sets.append(delivery_turns)
    scan_sets.append(worker_turns)
    for turns in scan_sets:
        chunks: list[str] = []
        for turn in sorted(turns, key=lambda t: int(t.get("turn_number") or 0)):
            frm = str(turn.get("from") or "").lower()
            body = str(turn.get("body") or "").strip()
            if not body or body.startswith("{"):
                continue
            if "cdp" in frm or "opus" in frm or "anthropic" in frm:
                chunks.append(body)
        if chunks:
            return chunks[-1]
    summary = closeout.get("summary")
    if isinstance(summary, str) and summary.strip():
        return summary
    for turns in scan_sets:
        for turn in reversed(turns):
            body = str(turn.get("body") or "").strip()
            if body and not body.startswith("{"):
                return body
    return ""


def provenance_from_cdp_harvest(
    result: CdpHarvestResult,
    *,
    consultant_family: str,
    consultant_substrate: str,
) -> ConsultProvenanceRecord | None:
    from scripts.model_manager.charter_control.r_verdict_gate import (
        consult_provenance_from_r_admit,
    )

    if result.escape_path:
        return None
    prov = consult_provenance_from_r_admit(
        consult_thread=result.consult_thread,
        harvest_text=result.harvest_text,
        consultant_family=consultant_family,
        consultant_substrate=consultant_substrate,
    )
    if prov is None:
        return None
    return ConsultProvenanceRecord(
        consult_thread=prov.consult_thread,
        verdict=prov.verdict,
        consultant_family=prov.consultant_family,
        consultant_substrate=prov.consultant_substrate,
        consultant_model=result.model_id,
        evidence_uri=result.harvest_text
        if result.harvest_text.startswith("cortex://")
        else None,
    )


__all__ = [
    "BACKOFF_SECONDS",
    "CDP_PRIMARY_MODEL",
    "CONSULT_EXHAUST_BUDGET_S",
    "CdpHarvestResult",
    "ConsultProvenanceRecord",
    "ConsultQueueRow",
    "backoff_s",
    "consult_role_for_row",
    "resolve_consult_role",
    "enqueue_consult",
    "sync_ledger_consult_queued",
    "load_consult_provenance",
    "load_queue_row",
    "parse_cdp_consult_harvest",
    "provenance_from_cdp_harvest",
    "write_consult_provenance",
]
