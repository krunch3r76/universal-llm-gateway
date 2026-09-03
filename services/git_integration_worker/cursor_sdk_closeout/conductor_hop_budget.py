"""Conductor hop budget enforcement (todo:conductor-hop-reactor R6).

Env-tunable caps from bind §2.6 item 6. Park path writes ``PARKED_TRANSPORT``
on the worker thread, emits ``hop.parked``, and pages the operator.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Any

from universal_logging import get_logger

logger = get_logger(__name__)

_HOP_ENTRY_GATE_KEY = "hop_entry_gate"
_HOP_WITNESSED_DONE_KEY = "hop_witnessed_done"
_HOP_PARKED_KEY = "hop_parked"
_HOP_PARK_REASON_KEY = "hop_park_reason"
_HOP_LAST_TERMINAL_AT_KEY = "hop_last_terminal_at"

_DEFAULT_CRASH_CAP = 3
_DEFAULT_NO_PROGRESS_CAP = 2
_DEFAULT_MISSION_CAP = 24
_DEFAULT_BACKOFF_S = (30.0, 120.0, 300.0)
_DEFAULT_REACTOR_GRACE_S = 120.0

_PARK_REASON_MISSION_CAP = "hop_budget_mission_cap"
_PARK_REASON_CRASH_CAP = "hop_budget_crash_cap"
_PARK_REASON_NO_PROGRESS_CAP = "hop_budget_no_progress_cap"


@dataclass(frozen=True, slots=True)
class HopBudgetConfig:
    crash_cap_per_row: int
    no_progress_cap: int
    mission_cap: int
    crash_backoff_s: tuple[float, ...]
    reactor_grace_s: float


@dataclass(frozen=True, slots=True)
class HopBudgetVerdict:
    ok: bool
    park: bool = False
    reason: str | None = None
    backoff_s: float = 0.0


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return max(0, int(raw))
    except ValueError:
        logger.warning("invalid %s=%r; using default %s", name, raw, default)
        return default


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return max(0.0, float(raw))
    except ValueError:
        logger.warning("invalid %s=%r; using default %s", name, raw, default)
        return default


def load_hop_budget_config() -> HopBudgetConfig:
    """Read env caps (bind §2.6.6 defaults)."""
    return HopBudgetConfig(
        crash_cap_per_row=_env_int("CONDUCTOR_HOP_CRASH_CAP_PER_ROW", _DEFAULT_CRASH_CAP),
        no_progress_cap=_env_int(
            "CONDUCTOR_HOP_NO_PROGRESS_CAP", _DEFAULT_NO_PROGRESS_CAP
        ),
        mission_cap=_env_int("CONDUCTOR_HOP_MISSION_CAP", _DEFAULT_MISSION_CAP),
        crash_backoff_s=_DEFAULT_BACKOFF_S,
        reactor_grace_s=_env_float(
            "CONDUCTOR_HOP_REACTOR_GRACE_S", _DEFAULT_REACTOR_GRACE_S
        ),
    )


def _record_data(record_json: str | None) -> dict[str, Any]:
    if not record_json:
        return {}
    try:
        data = json.loads(record_json)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _entry_gate_for_row(row: dict[str, Any]) -> str:
    record = _record_data(str(row.get("record_json") or ""))
    gate = record.get(_HOP_ENTRY_GATE_KEY)
    if isinstance(gate, str) and gate:
        return gate
    work_key = str(row.get("work_key") or "")
    if not work_key.startswith("todo:"):
        return "G1"
    slug = work_key.split(":", 1)[1].strip()
    if not slug:
        return "G1"
    try:
        from implement_admission.conductor_witness import (
            fold_scoreboard,
            resolve_entry_gate_from_fold,
        )
        from implement_admission.conductor_witness_defaults import DefaultWitnessCortex
        from implement_admission.conductor_witness_types import FoldDeps

        fold = fold_scoreboard(
            slug,
            deps=FoldDeps(cortex=DefaultWitnessCortex()),
            write_journal=False,
        )
        if fold is not None:
            return resolve_entry_gate_from_fold(fold)
    except Exception as exc:  # noqa: BLE001 — fold is advisory
        logger.warning("hop budget entry_gate fold failed slug=%s err=%s", slug, exc)
    return "G1"


def _witnessed_done_snapshot(row: dict[str, Any]) -> frozenset[str]:
    record = _record_data(str(row.get("record_json") or ""))
    raw = record.get(_HOP_WITNESSED_DONE_KEY)
    if isinstance(raw, list):
        return frozenset(str(v) for v in raw)
    work_key = str(row.get("work_key") or "")
    if not work_key.startswith("todo:"):
        return frozenset()
    slug = work_key.split(":", 1)[1].strip()
    if not slug:
        return frozenset()
    try:
        from implement_admission.conductor_witness import fold_scoreboard
        from implement_admission.conductor_witness_defaults import DefaultWitnessCortex
        from implement_admission.conductor_witness_types import FoldDeps

        fold = fold_scoreboard(
            slug,
            deps=FoldDeps(cortex=DefaultWitnessCortex()),
            write_journal=False,
        )
        if fold is not None:
            return fold.witnessed_done
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "hop budget witnessed_done fold failed slug=%s err=%s", slug, exc
        )
    return frozenset()


def _is_conductor_row(row: dict[str, Any]) -> bool:
    from services.git_integration_worker.cursor_sdk_conductor_conflict import (
        _record_packet_kind,
    )

    record_json = str(row.get("record_json") or "")
    return _record_packet_kind(record_json) == "conductor"


def list_mission_terminal_chain(
    *,
    work_key: str,
    exclude_dispatch_id: str | None = None,
) -> list[dict[str, Any]]:
    """Terminal conductor rows for one mission, oldest hop_seq first."""
    from services.git_integration_worker.cursor_dispatch_ledger import (
        CursorDispatchLedger,
    )

    ledger = CursorDispatchLedger.instance()
    with ledger._connect() as conn:
        rows = conn.execute(
            "SELECT * FROM cursor_sdk_dispatches "
            "WHERE work_key=? AND status IN ('completed','failed','cancelled') "
            "ORDER BY COALESCE(json_extract(record_json, '$.hop_seq'), 0), "
            "COALESCE(terminal_at, queued_at)",
            (work_key,),
        ).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        mapped = {k: row[k] for k in row.keys()}
        if exclude_dispatch_id and mapped.get("dispatch_id") == exclude_dispatch_id:
            continue
        if not _is_conductor_row(mapped):
            continue
        out.append(mapped)
    return out


def _planned_closeout(row: dict[str, Any], *, closeout_tokens: frozenset[str]) -> bool:
    if "ROW_HOP" in closeout_tokens:
        return True
    record = _record_data(str(row.get("record_json") or ""))
    tokens = record.get("closeout_stop_tokens")
    if isinstance(tokens, list) and "ROW_HOP" in tokens:
        return True
    return False


def _crash_backoff_s(*, crash_streak: int, config: HopBudgetConfig) -> float:
    if crash_streak <= 0:
        return 0.0
    idx = min(crash_streak - 1, len(config.crash_backoff_s) - 1)
    return config.crash_backoff_s[idx]


def evaluate_hop_budget(
    row: dict[str, Any],
    *,
    closeout_tokens: frozenset[str],
    config: HopBudgetConfig | None = None,
) -> HopBudgetVerdict:
    """Return whether the reactor may admit a successor (bind §2.6.6)."""
    cfg = config or load_hop_budget_config()
    work_key = str(row.get("work_key") or "")
    if not work_key:
        return HopBudgetVerdict(ok=True)

    record = _record_data(str(row.get("record_json") or ""))
    if record.get(_HOP_PARKED_KEY) is True:
        return HopBudgetVerdict(ok=False, park=False, reason=str(
            record.get(_HOP_PARK_REASON_KEY) or "already_parked"
        ))

    dispatch_id = str(row.get("dispatch_id") or "")
    chain = list_mission_terminal_chain(
        work_key=work_key, exclude_dispatch_id=None
    )
    mission_hops = len(chain)
    if cfg.mission_cap > 0 and mission_hops >= cfg.mission_cap:
        return HopBudgetVerdict(
            ok=False,
            park=True,
            reason=_PARK_REASON_MISSION_CAP,
        )

    entry_gate = _entry_gate_for_row(row)
    planned = _planned_closeout(row, closeout_tokens=closeout_tokens)
    witnessed = _witnessed_done_snapshot(row)

    if planned:
        witnessed_now = witnessed
        streak = 0
        last_witness = witnessed_now
        for prior in reversed(chain):
            if prior.get("dispatch_id") == dispatch_id:
                continue
            if _entry_gate_for_row(prior) != entry_gate:
                break
            prior_tokens = prior_record_tokens(prior)
            if not _planned_closeout(prior, closeout_tokens=prior_tokens):
                break
            prior_witness = _witnessed_done_snapshot(prior)
            if last_witness - prior_witness:
                break
            streak += 1
            last_witness = prior_witness
        if cfg.no_progress_cap > 0 and streak >= cfg.no_progress_cap:
            return HopBudgetVerdict(
                ok=False,
                park=True,
                reason=_PARK_REASON_NO_PROGRESS_CAP,
            )
        return HopBudgetVerdict(ok=True)

    crash_streak = 0
    for prior in reversed(chain):
        if prior.get("dispatch_id") == dispatch_id:
            continue
        prior_gate = _entry_gate_for_row(prior)
        if prior_gate != entry_gate:
            break
        prior_tokens = prior_record_tokens(prior)
        if _planned_closeout(prior, closeout_tokens=prior_tokens):
            break
        crash_streak += 1
    crash_streak += 1

    if cfg.crash_cap_per_row > 0 and crash_streak >= cfg.crash_cap_per_row:
        return HopBudgetVerdict(
            ok=False,
            park=True,
            reason=_PARK_REASON_CRASH_CAP,
        )
    return HopBudgetVerdict(
        ok=True,
        backoff_s=_crash_backoff_s(crash_streak=crash_streak, config=cfg),
    )


def prior_record_tokens(row: dict[str, Any]) -> frozenset[str]:
    record = _record_data(str(row.get("record_json") or ""))
    raw = record.get("closeout_stop_tokens")
    if isinstance(raw, list):
        return frozenset(str(t).upper() for t in raw)
    return frozenset()


def budget_ok_for_hop(
    row: dict[str, Any],
    *,
    closeout_tokens: frozenset[str] | None = None,
) -> bool:
    """``hop_owed`` budget gate — park and backoff handled in the reactor."""
    tokens = closeout_tokens or prior_record_tokens(row)
    verdict = evaluate_hop_budget(row, closeout_tokens=tokens)
    return verdict.ok and not verdict.park


def build_budget_authority_patch(row: dict[str, Any]) -> dict[str, Any]:
    """Snapshot fold state onto ``record_json`` at terminal evaluation."""
    witnessed = sorted(_witnessed_done_snapshot(row))
    return {
        _HOP_ENTRY_GATE_KEY: _entry_gate_for_row(row),
        _HOP_WITNESSED_DONE_KEY: witnessed,
        _HOP_LAST_TERMINAL_AT_KEY: time.time(),
    }


def build_parked_transport_body(*, reason: str, hop_seq: int | None) -> str:
    lines = [
        "status: complete",
        "stop: PARKED_TRANSPORT",
        f"reason: {reason}",
    ]
    if hop_seq is not None:
        lines.append(f"hop_seq: {hop_seq}")
    return "\n".join(lines) + "\n"


def default_park_poster(thread_id: str, body: str) -> None:
    """POST ``PARKED_TRANSPORT`` closeout shape on the worker thread."""
    import os

    import httpx
    from transport_utils import DEFAULT_AGENT_BUS_URL, make_sync_client

    token = os.environ.get("AGENT_BUS_TOKEN", "").strip()
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    payload = {
        "thread": thread_id,
        "from": "conductor-hop",
        "to": "cursor",
        "subject": f"stop: PARKED_TRANSPORT — {thread_id}",
        "body": body,
        "status": "open",
    }
    with make_sync_client(DEFAULT_AGENT_BUS_URL, timeout=15.0) as client:
        resp = client.post("/turns", json=payload, headers=headers)
    if resp.status_code >= 400:
        raise httpx.HTTPStatusError(
            f"park post failed status={resp.status_code}",
            request=resp.request,
            response=resp,
        )


async def page_hop_budget_parked(
    *,
    dispatch_id: str,
    thread_id: str,
    reason: str,
    work_key: str,
) -> bool:
    """Awareness page when hop budgets exhaust (bind §2.6.6 / §6)."""
    from pager_notify.client import notify_pager
    from pager_notify.mission_page import format_mission_awareness_page
    from pager_notify.so_what import clip, SMS_SUBJECT_MAX

    subject = clip(f"Conductor hop parked — {reason}", SMS_SUBJECT_MAX)
    _subj, body, tag = format_mission_awareness_page(
        subject=subject,
        vision=(
            "ULG conductor missions should chain across G-rows without you "
            "babysitting each crash or loop."
        ),
        looking_back=(
            f"Mission {work_key} on worker thread {thread_id} hit hop budget "
            f"{reason} at dispatch {dispatch_id}."
        ),
        architecture=(
            "git_integration_worker conductor_hop reactor stamped "
            "PARKED_TRANSPORT on the worker thread and emitted "
            "frontier.sdk.conductor.hop.parked."
        ),
        looking_ahead=(
            "Harvest the summoning thread; resume only after fixing the "
            "underlying row or resetting budget state."
        ),
        beyond_bullets=[
            "Liaison must not second-fire the successor — park is substrate-owned.",
        ],
        tag="conductor-hop-parked",
    )
    try:
        return await notify_pager(_subj, body, tag=tag)
    except Exception:  # noqa: BLE001
        logger.warning(
            "hop budget park pager failed dispatch=%s thread=%s",
            dispatch_id,
            thread_id,
            exc_info=True,
        )
        return False


async def park_conductor_hop_mission(
    row: dict[str, Any],
    *,
    reason: str,
    poster: Any | None = None,
) -> None:
    """Park path: worker-thread line, event, awareness page, ledger stamp."""
    from services.git_integration_worker.cursor_dispatch_ledger import (
        CursorDispatchLedger,
    )
    from services.git_integration_worker.cursor_sdk_hop_events import (
        emit_frontier_sdk_conductor_hop_parked,
    )
    from services.git_integration_worker.cursor_sdk_ledger_hop import (
        hop_fields_from_record_json,
    )

    dispatch_id = str(row.get("dispatch_id") or "")
    thread_id = str(row.get("thread_id") or "")
    work_key = str(row.get("work_key") or "")
    hop_fields = hop_fields_from_record_json(str(row.get("record_json") or ""))
    hop_seq = hop_fields.get("hop_seq")
    hop_seq_int = int(hop_seq) if isinstance(hop_seq, int) else None

    body = build_parked_transport_body(reason=reason, hop_seq=hop_seq_int)
    if thread_id:
        try:
            (poster or default_park_poster)(thread_id, body)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "conductor hop park bus post failed dispatch=%s thread=%s err=%s",
                dispatch_id,
                thread_id,
                exc,
            )

    emit_frontier_sdk_conductor_hop_parked(
        dispatch_id=dispatch_id,
        thread_id=thread_id,
        hop_seq=hop_seq_int or 0,
        reason=reason,
    )

    ledger = CursorDispatchLedger.instance()
    ledger.merge_record_json(
        dispatch_id=dispatch_id,
        patch={
            _HOP_PARKED_KEY: True,
            _HOP_PARK_REASON_KEY: reason,
            **build_budget_authority_patch(row),
        },
    )

    await page_hop_budget_parked(
        dispatch_id=dispatch_id,
        thread_id=thread_id,
        reason=reason,
        work_key=work_key,
    )


__all__ = [
    "HopBudgetConfig",
    "HopBudgetVerdict",
    "budget_ok_for_hop",
    "build_budget_authority_patch",
    "build_parked_transport_body",
    "default_park_poster",
    "evaluate_hop_budget",
    "list_mission_terminal_chain",
    "load_hop_budget_config",
    "park_conductor_hop_mission",
    "prior_record_tokens",
]
