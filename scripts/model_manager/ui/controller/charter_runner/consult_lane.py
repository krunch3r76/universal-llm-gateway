"""Durable consult queue — external fire, backoff, provenance (spec §B row 7)."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from libs.charter_runner_store.db import charter_runner_data_dir, execute_with_retry
from universal_logging import get_logger

from scripts.model_manager import observation_event as events

from . import bus_client, dispatch_client, window_log
from .admission import CapsView, EnvFacts, decide
from .admit import _count_admissions
from .caps import CapStore
from .env_snapshot import EnvSnapshot
from .materializer_consult import consult_subject, materialize_consult_packet
from .root_ledger import (
    RootLedgerRow,
    RootStatus,
    Transition,
    load_root,
    open_default_ledger,
    upsert_root,
    write_cortex_mirror,
)
from .seed_phase1 import PHASE1_SEEDS
from .telemetry import emit_consult_deferred, emit_consult_queued, emit_tick_transition

logger = get_logger(__name__)

MIGRATED_ROOTS = frozenset(seed.root_id for seed in PHASE1_SEEDS)
BACKOFF_SECONDS = (60.0, 300.0, 900.0, 3600.0)
CONSULT_EXHAUST_BUDGET_S = 86400.0
CDP_PRIMARY_MODEL = "cdp/opus-5"

_old_tick_admit_counts: dict[str, int] = {rid: 0 for rid in MIGRATED_ROOTS}


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
class KernelTickOutcome:
    old_decision_label: str
    admitted: bool = False
    skipped_reason: str | None = None


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


def is_kernel_migrated(root_id: str) -> bool:
    return root_id in MIGRATED_ROOTS


def old_tick_admit_count(root_id: str) -> int:
    return _old_tick_admit_counts.get(root_id, 0)


def record_old_tick_admit_blocked(root_id: str) -> None:
    """Filter caught an old-path admit attempt — count must stay zero."""
    if root_id in MIGRATED_ROOTS:
        logger.warning(
            "charter-runner old-tick admit blocked for kernel-migrated root=%s",
            root_id,
        )


def _consult_role_for_row(row: RootLedgerRow) -> str:
    if row.consult_role in {"r_admit", "judgment_gap"}:
        return row.consult_role
    lane = (row.pickup_lane or "judgment").lower()
    return "r_admit" if lane == "consult" else "judgment_gap"


def _backoff_s(attempts: int) -> float:
    idx = min(max(attempts, 1) - 1, len(BACKOFF_SECONDS) - 1)
    return BACKOFF_SECONDS[idx]


def _load_queue_row(conn, root_id: str, gid: str, role: str) -> ConsultQueueRow | None:
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


def enqueue_consult(
    conn,
    *,
    row: RootLedgerRow,
    consult_role: str,
    corpus_sha: str | None = None,
) -> ConsultQueueRow:
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
    return _load_queue_row(conn, row.root_id, gid, consult_role)  # type: ignore[return-value]


def _provenance_path(root_id: str) -> Path:
    return charter_runner_data_dir() / "consult-provenance" / f"{root_id}.json"


def write_consult_provenance(record: ConsultProvenanceRecord, *, root_id: str) -> str:
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
    path = _provenance_path(root_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    uri = f"cortex://notes/system/threads/charter-consult-provenance/{root_id}.json"
    mirror = Path.home() / ".local" / "share" / "cortex" / uri.removeprefix("cortex://")
    mirror.parent.mkdir(parents=True, exist_ok=True)
    mirror.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return uri


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
        if payload.get("project_ask_execution_id") or payload.get("transport") == "project_ask":
            return CdpHarvestResult(
                model_id=model_id or "project_ask",
                consult_thread=worker_thread,
                harvest_text="",
                escape_path=True,
            )
        return None
    harvest_text = _harvest_text_from_turns(worker_turns, payload)
    thread = str(payload.get("consult_thread") or worker_thread or "")
    if not harvest_text.strip():
        return None
    return CdpHarvestResult(
        model_id=model_id,
        consult_thread=thread,
        harvest_text=harvest_text,
        escape_path=False,
    )


def _harvest_text_from_turns(
    worker_turns: list[dict[str, Any]], closeout: dict[str, Any]
) -> str:
    for key in ("content_proof_uri", "archive_uri", "harvest_text", "summary"):
        val = closeout.get(key)
        if isinstance(val, str) and val.strip():
            return val
    chunks: list[str] = []
    for turn in sorted(worker_turns, key=lambda t: int(t.get("turn_number") or 0)):
        frm = str(turn.get("from") or "").lower()
        body = str(turn.get("body") or "").strip()
        if not body or body.startswith("{"):
            continue
        if "cdp" in frm or "opus" in frm or "anthropic" in frm:
            chunks.append(body)
    if chunks:
        return chunks[-1]
    for turn in reversed(worker_turns):
        body = str(turn.get("body") or "").strip()
        if body and not body.startswith("{"):
            return body
    return ""


def provenance_from_cdp_harvest(result: CdpHarvestResult) -> ConsultProvenanceRecord | None:
    from .r_verdict_gate import consult_provenance_from_r_admit

    if result.escape_path:
        return None
    prov = consult_provenance_from_r_admit(
        consult_thread=result.consult_thread,
        harvest_text=result.harvest_text,
    )
    if prov is None:
        return None
    return ConsultProvenanceRecord(
        consult_thread=prov.consult_thread,
        verdict=prov.verdict,
        consultant_family=prov.consultant_family,
        consultant_substrate=prov.consultant_substrate,
        consultant_model=result.model_id,
        evidence_uri=result.harvest_text if result.harvest_text.startswith("cortex://") else None,
    )


def _ledger_row_from_state(
    conn,
    root_id: str,
    *,
    status: RootStatus,
    transition: Transition,
    wip: str | None = None,
    consult_attempts: int | None = None,
    consult_next_retry: float | None = None,
) -> RootLedgerRow:
    existing = load_root(conn, root_id)
    if existing is None:
        raise KeyError(f"ledger row missing for {root_id}")
    row = RootLedgerRow(
        root_id=existing.root_id,
        status=status,
        pickup_gid=existing.pickup_gid,
        pickup_lane=existing.pickup_lane,
        pickup_executor=existing.pickup_executor,
        attendance=existing.attendance,
        scoreboard_uri=existing.scoreboard_uri,
        wip_window_id=wip if wip is not None else existing.wip_window_id,
        revise_count=existing.revise_count,
        consult_role=existing.consult_role or _consult_role_for_row(existing),
        consult_attempts=(
            consult_attempts
            if consult_attempts is not None
            else existing.consult_attempts
        ),
        consult_next_retry=(
            consult_next_retry
            if consult_next_retry is not None
            else existing.consult_next_retry
        ),
        consult_poll_from=existing.consult_poll_from,
        harvest_deadline=existing.harvest_deadline,
        last_window_id=existing.last_window_id,
        last_transition=transition.value,
        last_error=existing.last_error,
        env_facts_json=existing.env_facts_json,
        updated_at=time.time(),
    )
    upsert_root(conn, row)
    write_cortex_mirror(row)
    return row


async def _admit_consult_window(
    *,
    row: RootLedgerRow,
    turns: list[dict],
    caps: CapStore,
    workspace_root: Path,
    consult_role: str,
    on_admit,
) -> bool:
    from .checkpoint_parse import parse_checkpoint
    from .checkpoint_body import resolve_checkpoint_body

    checkpoint = next(
        (t for t in reversed(turns) if str(t.get("subject") or "").upper().startswith("CHECKPOINT")),
        None,
    )
    if checkpoint is None:
        return False
    body = resolve_checkpoint_body(
        str(checkpoint.get("body") or ""),
        sidecar_uri=(
            checkpoint.get("sidecar_uri")
            if isinstance(checkpoint.get("sidecar_uri"), str)
            else None
        ),
    )
    parsed = parse_checkpoint(body)
    window_index = _count_admissions(turns) + 1
    packet = materialize_consult_packet(
        row.root_id,
        parsed,
        scoreboard_uri=row.scoreboard_uri,
        window_index=window_index,
    )
    subject = consult_subject(row.root_id, window_index, consult_role=consult_role)
    caps.mark_admit_intent(row.root_id, window_index)
    result = await dispatch_client.fire_window(
        row.root_id,
        packet,
        workspace_root=workspace_root,
        window_index=window_index,
        subject=subject,
        admission_mode="consult",
        consult_role=consult_role,
    )
    caps.record_admit(row.root_id)
    worker_thread = str(result.get("thread_id") or "")
    caps.bind_intent_worker(row.root_id, window_index, worker_thread)
    now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    await bus_client.post_admission_pointer(
        row.root_id,
        window_index=window_index,
        posted_at_iso=now_iso,
        worker_thread=worker_thread,
        packet_path=str(result.get("packet_path") or ""),
        admission_mode="consult",
    )
    await events.emit_manage_charter_tick_admitted(
        root=row.root_id,
        dispatch_id=str(result.get("dispatch_id") or worker_thread),
        worker_thread=worker_thread,
    )
    window_log.append_admit(
        root_id=row.root_id,
        window_index=window_index,
        worker_thread=worker_thread,
        packet_path=str(result.get("packet_path") or ""),
        packet_text=packet,
        dispatch_id=str(result.get("dispatch_id") or ""),
    )
    executor = result.get("executor") or {}
    window_log.append_executor_note(worker_thread, executor)
    if on_admit is not None:
        try:
            on_admit(
                f"charter-runner kernel consult admitted {worker_thread} "
                f"root={row.root_id} role={consult_role} model={executor.get('reviewer_model')}"
            )
        except Exception:  # noqa: BLE001
            logger.exception("kernel consult on_admit failed")
    return True


async def apply_kernel_tick_for_root(
    root_id: str,
    turns: list[dict],
    *,
    caps: CapStore,
    workspace_root: Path | None,
    env: EnvSnapshot,
    on_admit=None,
) -> KernelTickOutcome:
    """Live kernel path for Phase-2 migrated roots."""
    conn = open_default_ledger()
    try:
        row = load_root(conn, root_id)
        if row is None:
            return KernelTickOutcome("kernel_unseeded")
        has_wip = any(
            str(t.get("subject") or "").upper().startswith("WIP CHARTER-RUNNER")
            for t in turns
        )
        facts = env.facts_for_root(root_id, has_wip=has_wip)
        facts = EnvFacts(
            substrate_up=facts.substrate_up,
            has_wip=facts.has_wip,
            attendance=row.attendance,
            propagation_residue=env.propagation_residue,
            giw_holder_lease=env.giw_holder_lease,
            restart_shaped=env.restart_shaped_for_root(root_id),
        )
        caps_view = CapsView.from_cap_store(caps, root_id)
        transition = decide(row, facts, caps_view)
        if transition == Transition.QUEUE_CONSULT:
            role = _consult_role_for_row(row)
            enqueue_consult(conn, row=row, consult_role=role)
            updated = _ledger_row_from_state(
                conn,
                root_id,
                status=RootStatus.CONSULT_QUEUED,
                transition=Transition.QUEUE_CONSULT,
            )
            await emit_consult_queued(root=root_id, gid=updated.pickup_gid or "?", role=role)
            await emit_tick_transition(
                root=root_id,
                from_status=row.status.value,
                to_status=updated.status.value,
                transition=transition.value,
                gid=updated.pickup_gid,
            )
            return KernelTickOutcome("kernel_queue_consult")
        if transition == Transition.DEFER_CONSULT:
            retry = time.time() + _backoff_s(max(row.consult_attempts, 1))
            _ledger_row_from_state(
                conn,
                root_id,
                status=RootStatus.CONSULT_DEFERRED,
                transition=Transition.DEFER_CONSULT,
                consult_next_retry=retry,
                consult_attempts=row.consult_attempts + 1,
            )
            await emit_consult_deferred(
                root=root_id, gid=row.pickup_gid or "?", next_retry=retry
            )
            return KernelTickOutcome("kernel_defer_consult")
        if transition == Transition.ADMIT_CONSULT:
            if workspace_root is None:
                return KernelTickOutcome("kernel_no_workspace")
            if row.consult_next_retry and time.time() < row.consult_next_retry:
                return KernelTickOutcome("kernel_consult_backoff")
            role = _consult_role_for_row(row)
            admitted = await _admit_consult_window(
                row=row,
                turns=turns,
                caps=caps,
                workspace_root=workspace_root,
                consult_role=role,
                on_admit=on_admit,
            )
            if admitted:
                _ledger_row_from_state(
                    conn,
                    root_id,
                    status=RootStatus.CONSULT_ADMITTED,
                    transition=Transition.ADMIT_CONSULT,
                    wip=f"charter-{root_id}-consult",
                )
            return KernelTickOutcome(
                "kernel_admit_consult" if admitted else "kernel_admit_failed",
                admitted=admitted,
            )
        return KernelTickOutcome(transition.value)
    finally:
        conn.close()


__all__ = [
    "BACKOFF_SECONDS",
    "CDP_PRIMARY_MODEL",
    "CdpHarvestResult",
    "ConsultProvenanceRecord",
    "ConsultQueueRow",
    "KernelTickOutcome",
    "MIGRATED_ROOTS",
    "apply_kernel_tick_for_root",
    "enqueue_consult",
    "is_kernel_migrated",
    "load_consult_provenance",
    "old_tick_admit_count",
    "parse_cdp_consult_harvest",
    "provenance_from_cdp_harvest",
    "record_old_tick_admit_blocked",
    "write_consult_provenance",
]
