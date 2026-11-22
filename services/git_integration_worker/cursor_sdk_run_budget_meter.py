"""In-row tool-call budget meter — warn steer and park at declared ceiling."""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

from universal_logging import get_logger

from services.git_integration_worker.cursor_sdk_park_for_restart import (
    ParkSignalResult,
    signal_park,
)
from services.git_integration_worker.cursor_sdk_run_limits import (
    RUN_BUDGET_CONTROL_KEY,
    RUN_BUDGET_EXHAUSTED_REASON,
    RUN_LIMITS_KEY,
    RunLimits,
    read_run_limits,
)
from services.git_integration_worker.cursor_sdk_steer_inject import (
    SteerDepositResult,
    deposit_steer_directive,
    poll_delivery_ack,
)

logger = get_logger(__name__)

BudgetClaimOutcome = Literal["claimed", "latched", "below_threshold", "missing", "unset"]


@dataclass(frozen=True, slots=True)
class RunBudgetReading:
    stream_tool_call_count: int
    budget_warn_deposited: bool
    budget_warn_delivered: bool
    budget_warn_delivered_at_tool_count: int | None
    budget_exhausted: bool
    budget_parked: bool


def _parse_record_data(record_json: str | Mapping[str, Any] | None) -> dict[str, Any]:
    if record_json is None:
        return {}
    if isinstance(record_json, str):
        if not record_json.strip():
            return {}
        try:
            data = json.loads(record_json)
        except json.JSONDecodeError:
            return {}
    else:
        data = dict(record_json)
    return data if isinstance(data, dict) else {}


def _control_blob(record_json: str | Mapping[str, Any] | None) -> dict[str, Any]:
    data = _parse_record_data(record_json)
    ctrl = data.get(RUN_BUDGET_CONTROL_KEY)
    return dict(ctrl) if isinstance(ctrl, dict) else {}


def merge_run_budget_control(
    ledger: Any,
    *,
    dispatch_id: str,
    patch: Mapping[str, Any],
) -> None:
    ledger.merge_record_json_subobject(
        dispatch_id=dispatch_id,
        subkey=RUN_BUDGET_CONTROL_KEY,
        patch=dict(patch),
    )


def read_run_budget_meter(
    record_json: str | Mapping[str, Any] | None,
) -> RunBudgetReading:
    ctrl = _control_blob(record_json)
    delivered_at = ctrl.get("budget_warn_delivered_at_tool_count")
    return RunBudgetReading(
        stream_tool_call_count=int(ctrl.get("stream_tool_call_count") or 0),
        budget_warn_deposited=bool(ctrl.get("budget_warn_deposited")),
        budget_warn_delivered=bool(ctrl.get("budget_warn_delivered")),
        budget_warn_delivered_at_tool_count=(
            int(delivered_at) if delivered_at is not None else None
        ),
        budget_exhausted=bool(ctrl.get("budget_exhausted")),
        budget_parked=bool(ctrl.get("budget_parked")),
    )


def run_budget_control_patch(blob: Mapping[str, Any]) -> dict[str, Any]:
    return {RUN_BUDGET_CONTROL_KEY: dict(blob)}


def build_budget_warn_directive() -> str:
    return "\n".join(
        [
            "CHECKPOINT — declared tool-call budget warning.",
            "You have reached the warn threshold for this dispatch.",
            "Write your closeout record now; additional tool calls may be limited.",
            "stop: BUDGET_WRAP_UP",
        ]
    )


def _limits_from_record(record_json: str) -> RunLimits | None:
    return read_run_limits(record_json)


def should_warn_budget(*, count: int, limits: RunLimits) -> bool:
    if limits.tool_call_budget is None or limits.warn_at is None:
        return False
    return count >= limits.warn_at


def should_exhaust_budget(*, count: int, limits: RunLimits) -> bool:
    if limits.tool_call_budget is None:
        return False
    return count >= limits.tool_call_budget


def _load_row_dict(ledger: Any, dispatch_id: str) -> dict[str, Any] | None:
    with ledger._connect() as conn:
        row = conn.execute(
            "SELECT * FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            (dispatch_id,),
        ).fetchone()
    if row is None:
        return None
    return {k: row[k] for k in row.keys()}


def try_claim_budget_warn_deposit(
    *,
    dispatch_id: str,
    claim_token: str,
    tool_call_count: int,
    ledger: Any | None = None,
) -> tuple[BudgetClaimOutcome, dict[str, Any] | None]:
    from services.git_integration_worker.cursor_dispatch_ledger import (
        CursorDispatchLedger,
        _merge_record_json_subobject_conn,
    )

    led = ledger or CursorDispatchLedger.instance()
    with led._connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT * FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            (dispatch_id,),
        ).fetchone()
        if row is None:
            conn.execute("ROLLBACK")
            return "missing", None
        mapped = {k: row[k] for k in row.keys()}
        record_json = str(mapped.get("record_json") or "")
        limits = _limits_from_record(record_json)
        if limits is None or limits.tool_call_budget is None:
            conn.execute("COMMIT")
            return "unset", mapped
        ctrl = _control_blob(record_json)
        if ctrl.get("budget_warn_deposited"):
            conn.execute("COMMIT")
            return "latched", mapped
        if ctrl.get("budget_warn_deposit_inflight"):
            conn.execute("COMMIT")
            return "latched", mapped
        if not should_warn_budget(count=tool_call_count, limits=limits):
            conn.execute("COMMIT")
            return "below_threshold", mapped
        _merge_record_json_subobject_conn(
            conn,
            dispatch_id=dispatch_id,
            subkey=RUN_BUDGET_CONTROL_KEY,
            patch={"budget_warn_deposit_inflight": claim_token},
        )
        conn.execute("COMMIT")
        refreshed = conn.execute(
            "SELECT * FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            (dispatch_id,),
        ).fetchone()
        out = {k: refreshed[k] for k in refreshed.keys()} if refreshed else mapped
        return "claimed", out


def finalize_budget_warn_deposit(
    *,
    dispatch_id: str,
    claim_token: str,
    deposit: SteerDepositResult,
    ledger: Any | None = None,
) -> None:
    from services.git_integration_worker.cursor_dispatch_ledger import (
        CursorDispatchLedger,
        _merge_record_json_subobject_conn,
    )

    led = ledger or CursorDispatchLedger.instance()
    with led._connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT record_json FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            (dispatch_id,),
        ).fetchone()
        if row is None:
            conn.execute("ROLLBACK")
            return
        ctrl = _control_blob(str(row["record_json"] or ""))
        if ctrl.get("budget_warn_deposit_inflight") not in (None, claim_token):
            conn.execute("ROLLBACK")
            return
        _merge_record_json_subobject_conn(
            conn,
            dispatch_id=dispatch_id,
            subkey=RUN_BUDGET_CONTROL_KEY,
            patch={
                "budget_warn_deposit_inflight": None,
                "budget_warn_deposited": True,
                "budget_warn_entry_id": deposit.entry_id,
                "budget_warn_authority_turn_id": deposit.authority_turn_id,
            },
        )
        conn.execute("COMMIT")


def release_budget_warn_claim(
    *,
    dispatch_id: str,
    claim_token: str,
    ledger: Any | None = None,
) -> None:
    from services.git_integration_worker.cursor_dispatch_ledger import (
        CursorDispatchLedger,
        _merge_record_json_subobject_conn,
    )

    led = ledger or CursorDispatchLedger.instance()
    with led._connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT record_json FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            (dispatch_id,),
        ).fetchone()
        if row is None:
            conn.execute("ROLLBACK")
            return
        ctrl = _control_blob(str(row["record_json"] or ""))
        if ctrl.get("budget_warn_deposit_inflight") == claim_token:
            _merge_record_json_subobject_conn(
                conn,
                dispatch_id=dispatch_id,
                subkey=RUN_BUDGET_CONTROL_KEY,
                patch={"budget_warn_deposit_inflight": None},
            )
        conn.execute("COMMIT")


def maybe_deposit_budget_warn(
    row: Mapping[str, Any],
    *,
    tool_call_count: int,
    submitted_id: str,
    actor: str = "run-budget",
    ledger: Any | None = None,
) -> SteerDepositResult | None:
    dispatch_id = str(row.get("dispatch_id") or "")
    claim_token = f"{submitted_id}:{uuid.uuid4().hex}"
    outcome, claimed_row = try_claim_budget_warn_deposit(
        dispatch_id=dispatch_id,
        claim_token=claim_token,
        tool_call_count=tool_call_count,
        ledger=ledger,
    )
    if outcome != "claimed" or claimed_row is None:
        return None
    thread_id = str(claimed_row.get("thread_id") or "")
    directive = build_budget_warn_directive()
    try:
        deposit = deposit_steer_directive(
            dispatch_id=dispatch_id,
            submitted_id=claim_token,
            thread_id=thread_id,
            directive=directive,
            reason="run-budget-warn",
            actor=actor,
        )
    except Exception:
        release_budget_warn_claim(
            dispatch_id=dispatch_id, claim_token=claim_token, ledger=ledger
        )
        raise
    finalize_budget_warn_deposit(
        dispatch_id=dispatch_id,
        claim_token=claim_token,
        deposit=deposit,
        ledger=ledger,
    )
    return deposit


def mark_budget_warn_delivered(
    *,
    dispatch_id: str,
    tool_call_count: int,
    ledger: Any | None = None,
) -> None:
    from services.git_integration_worker.cursor_dispatch_ledger import (
        CursorDispatchLedger,
    )

    merge_run_budget_control(
        ledger or CursorDispatchLedger.instance(),
        dispatch_id=dispatch_id,
        patch={
            "budget_warn_delivered": True,
            "budget_warn_delivered_at_tool_count": tool_call_count,
        },
    )


def sync_budget_warn_delivery(
    row: Mapping[str, Any],
    *,
    tool_call_count: int,
) -> bool:
    record_json = str(row.get("record_json") or "")
    ctrl = _control_blob(record_json)
    if ctrl.get("budget_warn_delivered"):
        return True
    entry_id = ctrl.get("budget_warn_entry_id")
    if not entry_id:
        return False
    dispatch_id = str(row.get("dispatch_id") or "")
    deposit = SteerDepositResult(
        dispatch_id=dispatch_id,
        entry_id=str(entry_id),
        authority_turn_id=str(ctrl.get("budget_warn_authority_turn_id") or ""),
        spool_path="",
    )
    if not deposit.authority_turn_id:
        return False
    ack = poll_delivery_ack(deposit)
    if ack is None:
        return False
    mark_budget_warn_delivered(dispatch_id=dispatch_id, tool_call_count=tool_call_count)
    return True


def maybe_park_budget_exhausted(
    row: Mapping[str, Any],
    *,
    tool_call_count: int,
    actor: str = "run-budget",
) -> ParkSignalResult | None:
    record_json = str(row.get("record_json") or "")
    limits = _limits_from_record(record_json)
    if limits is None or limits.tool_call_budget is None:
        return None
    reading = read_run_budget_meter(record_json)
    if reading.budget_parked or reading.budget_exhausted:
        return None
    if not should_exhaust_budget(count=tool_call_count, limits=limits):
        return None
    dispatch_id = str(row.get("dispatch_id") or "")
    result = signal_park(
        dispatch_id,
        intent_id=None,
        drain_epoch=None,
        actor=actor,
        reason=RUN_BUDGET_EXHAUSTED_REASON,
    )
    from services.git_integration_worker.cursor_dispatch_ledger import (
        CursorDispatchLedger,
    )

    merge_run_budget_control(
        CursorDispatchLedger.instance(),
        dispatch_id=dispatch_id,
        patch={"budget_exhausted": True, "budget_parked": True},
    )
    return result


class RunBudgetStreamHook:
    """Increment ``stream_tool_call_count`` during ``observe_run_stream``."""

    __slots__ = ("_dispatch_id", "_ledger", "_stream_tool_call_count")

    def __init__(
        self,
        *,
        dispatch_id: str,
        ledger: Any | None = None,
    ) -> None:
        from services.git_integration_worker.cursor_dispatch_ledger import (
            CursorDispatchLedger,
        )

        self._dispatch_id = dispatch_id
        self._ledger = ledger or CursorDispatchLedger.instance()
        self._stream_tool_call_count = 0
        self._load_existing_state()

    def _load_existing_state(self) -> None:
        row = _load_row_dict(self._ledger, self._dispatch_id)
        if row is None:
            return
        reading = read_run_budget_meter(str(row.get("record_json") or ""))
        self._stream_tool_call_count = reading.stream_tool_call_count

    def note_tool_call(self, *, status: str) -> None:
        if status not in {"completed", "error"}:
            return
        self._stream_tool_call_count += 1
        merge_run_budget_control(
            self._ledger,
            dispatch_id=self._dispatch_id,
            patch={"stream_tool_call_count": self._stream_tool_call_count},
        )

    def flush(self) -> None:
        return


def maybe_tick_run_budget_meter(
    *,
    dispatch_id: str,
    tool_call_count: int,
    ledger: Any | None = None,
) -> None:
    from services.git_integration_worker.cursor_dispatch_ledger import (
        CursorDispatchLedger,
    )

    led = ledger or CursorDispatchLedger.instance()
    row = _load_row_dict(led, dispatch_id)
    if row is None:
        return
    record_json = str(row.get("record_json") or "")
    if RUN_LIMITS_KEY not in _parse_record_data(record_json):
        return
    row = {**row, "dispatch_id": dispatch_id}
    sync_budget_warn_delivery(row, tool_call_count=tool_call_count)
    maybe_deposit_budget_warn(
        row,
        tool_call_count=tool_call_count,
        submitted_id=f"run-budget:{dispatch_id}",
        ledger=led,
    )
    maybe_park_budget_exhausted(row, tool_call_count=tool_call_count)


class CombinedToolCallStreamHooks:
    """Forward tool-call / prose notifications to multiple stream hooks."""

    __slots__ = ("_hooks",)

    def __init__(self, *hooks: Any) -> None:
        self._hooks = [hook for hook in hooks if hook is not None]

    def note_prose(self, text: str) -> None:
        for hook in self._hooks:
            note = getattr(hook, "note_prose", None)
            if callable(note):
                note(text)

    def note_tool_call(self, **kwargs: Any) -> None:
        for hook in self._hooks:
            note = getattr(hook, "note_tool_call", None)
            if callable(note):
                note(**kwargs)

    def flush(self) -> None:
        for hook in self._hooks:
            flush = getattr(hook, "flush", None)
            if callable(flush):
                flush()


__all__ = [
    "CombinedToolCallStreamHooks",
    "RunBudgetReading",
    "RunBudgetStreamHook",
    "build_budget_warn_directive",
    "maybe_deposit_budget_warn",
    "maybe_park_budget_exhausted",
    "maybe_tick_run_budget_meter",
    "merge_run_budget_control",
    "read_run_budget_meter",
    "run_budget_control_patch",
    "sync_budget_warn_delivery",
]
