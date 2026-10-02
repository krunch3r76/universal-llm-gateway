"""In-row density harness — chars/4 visible context meter (GIW stream source).

Generic reader over ``record_json``; conductor-only steer is gated in
``conductor_hop_watchdog`` (contract=conductor). Trigger: latest visible
estimate >= visible_fraction × ``context_window_tokens(model)`` — not a
cumulative-sum arm (operator ruling 2026-10-02).

Trajectory lives under ``record_json.density_harness``; steer latch, delivery,
park, and stream tool-call tally live under ``record_json.density_harness_control``
so stream writes never clobber the deposit latch.
"""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

from cursor_capabilities import context_window_tokens
from universal_logging import get_logger

from services.git_integration_worker.cursor_sdk_park_for_restart import (
    ParkSignalResult,
    signal_park,
)
from services.git_integration_worker.cursor_sdk_steer_inject import (
    SteerDepositResult,
    deposit_steer_directive,
    poll_delivery_ack,
)

logger = get_logger(__name__)

_DENSITY_TRAJECTORY_KEY = "density_harness"
_DENSITY_CONTROL_KEY = "density_harness_control"
_SERIES_CAP = 200
_DEFAULT_VISIBLE_FRACTION = 0.30
_IGNORED_STEER_TOOL_CALLS = 8
_CHARS_PER_TOKEN_DIVISOR = 4

ClaimOutcome = Literal["claimed", "latched", "below_threshold", "missing"]


@dataclass(frozen=True, slots=True)
class DensityHarnessConfig:
    visible_fraction: float


@dataclass(frozen=True, slots=True)
class DensityMeterReading:
    model_calls: int
    latest_visible_estimate: int
    running_visible_sum: int
    visible_series: tuple[tuple[int, int], ...]
    steer_deposited: bool
    steer_delivered: bool
    steer_delivered_at_tool_count: int | None
    steer_parked: bool
    stream_tool_call_count: int


def load_density_harness_config() -> DensityHarnessConfig:
    raw = os.environ.get("GIW_DENSITY_HARNESS_VISIBLE_FRACTION", "").strip()
    if not raw:
        return DensityHarnessConfig(visible_fraction=_DEFAULT_VISIBLE_FRACTION)
    try:
        fraction = float(raw)
    except ValueError:
        logger.warning(
            "invalid GIW_DENSITY_HARNESS_VISIBLE_FRACTION=%r; using %s",
            raw,
            _DEFAULT_VISIBLE_FRACTION,
        )
        return DensityHarnessConfig(visible_fraction=_DEFAULT_VISIBLE_FRACTION)
    if fraction <= 0 or fraction > 1:
        logger.warning(
            "GIW_DENSITY_HARNESS_VISIBLE_FRACTION=%s out of (0,1]; using %s",
            fraction,
            _DEFAULT_VISIBLE_FRACTION,
        )
        return DensityHarnessConfig(visible_fraction=_DEFAULT_VISIBLE_FRACTION)
    return DensityHarnessConfig(visible_fraction=fraction)


def estimate_tokens_from_chars(char_count: int) -> int:
    if char_count <= 0:
        return 0
    return char_count // _CHARS_PER_TOKEN_DIVISOR


def estimate_tokens_from_text(text: str) -> int:
    return estimate_tokens_from_chars(len(text or ""))


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


def _trajectory_blob(record_json: str | Mapping[str, Any] | None) -> dict[str, Any]:
    data = _parse_record_data(record_json)
    blob = data.get(_DENSITY_TRAJECTORY_KEY)
    if not isinstance(blob, dict):
        return {}
    out = dict(blob)
    for legacy in (
        "density_steer_deposited",
        "density_steer_entry_id",
        "density_steer_authority_turn_id",
        "density_steer_delivered",
        "density_steer_delivered_at_tool_count",
        "density_steer_parked",
    ):
        out.pop(legacy, None)
    return out


def _control_blob(record_json: str | Mapping[str, Any] | None) -> dict[str, Any]:
    data = _parse_record_data(record_json)
    ctrl = data.get(_DENSITY_CONTROL_KEY)
    if isinstance(ctrl, dict):
        return dict(ctrl)
    traj = data.get(_DENSITY_TRAJECTORY_KEY)
    if not isinstance(traj, dict):
        return {}
    migrated: dict[str, Any] = {}
    for key in (
        "density_steer_deposited",
        "density_steer_entry_id",
        "density_steer_authority_turn_id",
        "density_steer_delivered",
        "density_steer_delivered_at_tool_count",
        "density_steer_parked",
        "density_steer_deposit_inflight",
        "stream_tool_call_count",
    ):
        if key in traj:
            migrated[key] = traj[key]
    return migrated


def merge_density_trajectory(
    ledger: Any,
    *,
    dispatch_id: str,
    patch: Mapping[str, Any],
) -> None:
    ledger.merge_record_json_subobject(
        dispatch_id=dispatch_id,
        subkey=_DENSITY_TRAJECTORY_KEY,
        patch=dict(patch),
    )


def merge_density_control(
    ledger: Any,
    *,
    dispatch_id: str,
    patch: Mapping[str, Any],
) -> None:
    ledger.merge_record_json_subobject(
        dispatch_id=dispatch_id,
        subkey=_DENSITY_CONTROL_KEY,
        patch=dict(patch),
    )


def read_density_harness_meter(
    record_json: str | Mapping[str, Any] | None,
    *,
    model: str,
) -> DensityMeterReading:
    """Reader for any dispatch id + model (no conductor gate here)."""
    _ = model
    blob = _trajectory_blob(record_json)
    ctrl = _control_blob(record_json)
    series_raw = blob.get("visible_series") or []
    series: list[tuple[int, int]] = []
    if isinstance(series_raw, list):
        for item in series_raw:
            if (
                isinstance(item, (list, tuple))
                and len(item) == 2
                and isinstance(item[0], int)
                and isinstance(item[1], int)
            ):
                series.append((item[0], item[1]))
    return DensityMeterReading(
        model_calls=int(blob.get("model_calls") or 0),
        latest_visible_estimate=int(blob.get("latest_visible_estimate") or 0),
        running_visible_sum=int(blob.get("running_visible_sum") or 0),
        visible_series=tuple(series),
        steer_deposited=bool(ctrl.get("density_steer_deposited")),
        steer_delivered=bool(ctrl.get("density_steer_delivered")),
        steer_delivered_at_tool_count=(
            int(ctrl["density_steer_delivered_at_tool_count"])
            if ctrl.get("density_steer_delivered_at_tool_count") is not None
            else None
        ),
        steer_parked=bool(ctrl.get("density_steer_parked")),
        stream_tool_call_count=int(ctrl.get("stream_tool_call_count") or 0),
    )


def steer_threshold_tokens(*, model: str, config: DensityHarnessConfig | None = None) -> int | None:
    window = context_window_tokens(model)
    if window is None:
        return None
    cfg = config or load_density_harness_config()
    return int(window * cfg.visible_fraction)


def should_steer_density_hop(
    reading: DensityMeterReading,
    *,
    model: str,
    config: DensityHarnessConfig | None = None,
) -> bool:
    threshold = steer_threshold_tokens(model=model, config=config)
    if threshold is None:
        return False
    if reading.steer_deposited:
        return False
    return reading.latest_visible_estimate >= threshold


def build_density_steer_directive(row: Mapping[str, Any]) -> str:
    record_json = str(row.get("record_json") or "")
    try:
        rec = json.loads(record_json) if record_json else {}
    except json.JSONDecodeError:
        rec = {}
    if not isinstance(rec, dict):
        rec = {}
    next_pickup = (
        rec.get("hop_entry_gate")
        or (rec.get("generation_options") or {}).get("scoreboard_entry_gate")
        or "same open G-row"
    )
    hop_seq = rec.get("hop_seq") or rec.get("closeout_hop_seq") or 1
    lines = [
        "CHECKPOINT — density harness context saturation.",
        f"Next-pickup: {next_pickup} (same row; planned in-row density hop).",
        "Anchor: this dispatch.",
        "Hop: in-row density hop — substrate admits successor on this thread.",
        "Mission: unchanged.",
        "Rows: continue the open G-row after hop.",
        "In-flight: close this row cleanly.",
        "Judgment: visible context crossed the harness threshold.",
        "NEXT_ADMIT: substrate-owned after terminal.",
        "RESUME: successor on this thread.",
        f"hop_seq: {hop_seq}",
        "stop: ROW_HOP",
    ]
    return "\n".join(lines)


def density_harness_record_patch(blob: Mapping[str, Any]) -> dict[str, Any]:
    """Legacy helper: trajectory-only patch (tests)."""
    return {_DENSITY_TRAJECTORY_KEY: dict(blob)}


def density_harness_control_patch(blob: Mapping[str, Any]) -> dict[str, Any]:
    return {_DENSITY_CONTROL_KEY: dict(blob)}


def _resolve_model(row: Mapping[str, Any]) -> str:
    model = str(row.get("resolved_model") or row.get("model") or "")
    if model.startswith("cursor/"):
        return model
    record_json = str(row.get("record_json") or "")
    try:
        rec = json.loads(record_json) if record_json else {}
        if isinstance(rec, dict) and rec.get("model"):
            return str(rec["model"])
    except json.JSONDecodeError:
        pass
    return model


def _load_row_dict(ledger: Any, dispatch_id: str) -> dict[str, Any] | None:
    with ledger._connect() as conn:
        row = conn.execute(
            "SELECT * FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            (dispatch_id,),
        ).fetchone()
    if row is None:
        return None
    return {k: row[k] for k in row.keys()}


def try_claim_density_steer_deposit(
    *,
    dispatch_id: str,
    claim_token: str,
    ledger: Any | None = None,
) -> tuple[ClaimOutcome, dict[str, Any] | None]:
    """Atomically reserve exactly-one steer deposit (``BEGIN IMMEDIATE``)."""
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
        ctrl = _control_blob(record_json)
        if ctrl.get("density_steer_deposited"):
            conn.execute("COMMIT")
            return "latched", mapped
        if ctrl.get("density_steer_deposit_inflight"):
            conn.execute("COMMIT")
            return "latched", mapped
        model = _resolve_model(mapped)
        reading = read_density_harness_meter(record_json, model=model)
        if not should_steer_density_hop(reading, model=model):
            conn.execute("COMMIT")
            return "below_threshold", mapped
        _merge_record_json_subobject_conn(
            conn,
            dispatch_id=dispatch_id,
            subkey=_DENSITY_CONTROL_KEY,
            patch={"density_steer_deposit_inflight": claim_token},
        )
        conn.execute("COMMIT")
        refreshed = conn.execute(
            "SELECT * FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            (dispatch_id,),
        ).fetchone()
        out = {k: refreshed[k] for k in refreshed.keys()} if refreshed else mapped
        return "claimed", out


def release_density_steer_claim(
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
        if ctrl.get("density_steer_deposit_inflight") == claim_token:
            _merge_record_json_subobject_conn(
                conn,
                dispatch_id=dispatch_id,
                subkey=_DENSITY_CONTROL_KEY,
                patch={"density_steer_deposit_inflight": None},
            )
        conn.execute("COMMIT")


def finalize_density_steer_deposit(
    *,
    dispatch_id: str,
    claim_token: str,
    deposit: SteerDepositResult,
    ledger: Any | None = None,
) -> None:
    from services.git_integration_worker.cursor_dispatch_ledger import (
        CursorDispatchLedger,
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
        if ctrl.get("density_steer_deposit_inflight") not in (None, claim_token):
            conn.execute("ROLLBACK")
            return
        from services.git_integration_worker.cursor_dispatch_ledger import (
            _merge_record_json_subobject_conn,
        )

        _merge_record_json_subobject_conn(
            conn,
            dispatch_id=dispatch_id,
            subkey=_DENSITY_CONTROL_KEY,
            patch={
                "density_steer_deposit_inflight": None,
                "density_steer_deposited": True,
                "density_steer_entry_id": deposit.entry_id,
                "density_steer_authority_turn_id": deposit.authority_turn_id,
            },
        )
        conn.execute("COMMIT")


class DensityHarnessStreamHook:
    """Accumulate chars/4 during ``observe_run_stream``; persist at call boundaries."""

    __slots__ = (
        "_dispatch_id",
        "_ledger",
        "_user_tokens",
        "_stream_tokens",
        "_model_calls",
        "_running_sum",
        "_series",
        "_tools_since_prose",
        "_started_call",
        "_stream_tool_call_count",
        "_dirty_trajectory",
        "_resume_mid_call",
    )

    def __init__(
        self,
        *,
        dispatch_id: str,
        user_text: str = "",
        ledger: Any | None = None,
    ) -> None:
        from services.git_integration_worker.cursor_dispatch_ledger import (
            CursorDispatchLedger,
        )

        self._dispatch_id = dispatch_id
        self._ledger = ledger or CursorDispatchLedger.instance()
        self._user_tokens = estimate_tokens_from_text(user_text)
        self._stream_tokens = 0
        self._model_calls = 0
        self._running_sum = 0
        self._series: list[list[int]] = []
        self._tools_since_prose = 0
        self._started_call = False
        self._stream_tool_call_count = 0
        self._dirty_trajectory = False
        self._resume_mid_call = False
        self._load_existing_state()

    @property
    def latest_visible_estimate(self) -> int:
        return self._user_tokens + self._stream_tokens

    def _load_existing_state(self) -> None:
        row = _load_row_dict(self._ledger, self._dispatch_id)
        if row is None:
            return
        record_json = str(row.get("record_json") or "")
        reading = read_density_harness_meter(record_json, model="")
        self._model_calls = reading.model_calls
        self._running_sum = reading.running_visible_sum
        self._series = [list(pair) for pair in reading.visible_series]
        self._stream_tool_call_count = reading.stream_tool_call_count
        latest = reading.latest_visible_estimate
        if latest > self._user_tokens:
            self._stream_tokens = latest - self._user_tokens
        self._resume_mid_call = reading.model_calls > len(reading.visible_series)
        self._started_call = False

    def _ensure_call_open(self) -> None:
        if self._started_call:
            return
        if self._resume_mid_call:
            self._started_call = True
            self._resume_mid_call = False
            return
        self._open_model_call()

    def note_prose(self, text: str) -> None:
        if not str(text or "").strip():
            return
        if self._tools_since_prose > 0 and self._started_call:
            self._close_model_call()
        self._ensure_call_open()
        self._stream_tokens += estimate_tokens_from_text(text)
        self._tools_since_prose = 0
        self._dirty_trajectory = True

    def note_tool_call(self, *, arg_bytes: int, result_bytes: int, status: str) -> None:
        if status not in {"completed", "error"}:
            return
        self._ensure_call_open()
        self._stream_tokens += estimate_tokens_from_chars(arg_bytes + result_bytes)
        self._tools_since_prose += 1
        self._stream_tool_call_count += 1
        self._dirty_trajectory = True
        merge_density_control(
            self._ledger,
            dispatch_id=self._dispatch_id,
            patch={"stream_tool_call_count": self._stream_tool_call_count},
        )

    def flush(self) -> None:
        if self._started_call:
            self._close_model_call()
        elif self._dirty_trajectory:
            self._persist_trajectory()

    def _open_model_call(self) -> None:
        self._started_call = True
        self._model_calls += 1

    def _close_model_call(self) -> None:
        if not self._started_call:
            return
        latest = self.latest_visible_estimate
        self._running_sum += latest
        self._append_series(self._model_calls, latest)
        self._started_call = False
        self._tools_since_prose = 0
        self._persist_trajectory()

    def _append_series(self, call_index: int, estimate: int) -> None:
        self._series.append([call_index, estimate])
        if len(self._series) > _SERIES_CAP:
            self._series = self._series[-_SERIES_CAP:]

    def _persist_trajectory(self) -> None:
        if not self._dirty_trajectory and self._model_calls == 0:
            return
        merge_density_trajectory(
            self._ledger,
            dispatch_id=self._dispatch_id,
            patch={
                "model_calls": self._model_calls,
                "latest_visible_estimate": self.latest_visible_estimate,
                "running_visible_sum": self._running_sum,
                "visible_series": list(self._series),
            },
        )
        self._dirty_trajectory = False


def apply_synthetic_density_trajectory(
    *,
    dispatch_id: str,
    call_estimates: list[int],
    ledger: Any | None = None,
) -> None:
    """Test helper: write a trajectory as if stream boundaries fired."""
    from services.git_integration_worker.cursor_dispatch_ledger import (
        CursorDispatchLedger,
    )

    led = ledger or CursorDispatchLedger.instance()
    series = [[i + 1, est] for i, est in enumerate(call_estimates)]
    if len(series) > _SERIES_CAP:
        series = series[-_SERIES_CAP:]
    latest = call_estimates[-1] if call_estimates else 0
    merge_density_trajectory(
        led,
        dispatch_id=dispatch_id,
        patch={
            "model_calls": len(call_estimates),
            "latest_visible_estimate": latest,
            "running_visible_sum": sum(call_estimates),
            "visible_series": series,
        },
    )


def maybe_deposit_density_steer(
    row: Mapping[str, Any],
    *,
    submitted_id: str,
    actor: str = "density-harness",
    ledger: Any | None = None,
) -> SteerDepositResult | None:
    dispatch_id = str(row.get("dispatch_id") or "")
    base = submitted_id or f"density-harness:{dispatch_id}"
    claim_token = f"{base}:{uuid.uuid4().hex}"
    outcome, claimed_row = try_claim_density_steer_deposit(
        dispatch_id=dispatch_id,
        claim_token=claim_token,
        ledger=ledger,
    )
    if outcome != "claimed" or claimed_row is None:
        return None
    thread_id = str(claimed_row.get("thread_id") or "")
    directive = build_density_steer_directive(claimed_row)
    try:
        deposit = deposit_steer_directive(
            dispatch_id=dispatch_id,
            submitted_id=claim_token,
            thread_id=thread_id,
            directive=directive,
            reason="density-harness-visible-context",
            actor=actor,
        )
    except Exception:
        release_density_steer_claim(
            dispatch_id=dispatch_id, claim_token=claim_token, ledger=ledger
        )
        raise
    finalize_density_steer_deposit(
        dispatch_id=dispatch_id,
        claim_token=claim_token,
        deposit=deposit,
        ledger=ledger,
    )
    return deposit


def mark_density_steer_delivered(
    *,
    dispatch_id: str,
    tool_call_count: int,
    ledger: Any | None = None,
) -> None:
    from services.git_integration_worker.cursor_dispatch_ledger import (
        CursorDispatchLedger,
    )

    led = ledger or CursorDispatchLedger.instance()
    merge_density_control(
        led,
        dispatch_id=dispatch_id,
        patch={
            "density_steer_delivered": True,
            "density_steer_delivered_at_tool_count": tool_call_count,
        },
    )


def _effective_tool_call_count(
    reading: DensityMeterReading,
    tool_call_count: int | None,
) -> int:
    if tool_call_count is not None:
        return tool_call_count
    return reading.stream_tool_call_count


def maybe_park_ignored_density_steer(
    row: Mapping[str, Any],
    *,
    tool_call_count: int | None = None,
    actor: str = "density-harness",
) -> ParkSignalResult | None:
    reading = read_density_harness_meter(
        str(row.get("record_json") or ""),
        model=_resolve_model(row),
    )
    if not reading.steer_deposited or not reading.steer_delivered:
        return None
    if reading.steer_parked:
        return None
    baseline = reading.steer_delivered_at_tool_count
    if baseline is None:
        return None
    effective = _effective_tool_call_count(reading, tool_call_count)
    if effective - baseline < _IGNORED_STEER_TOOL_CALLS:
        return None
    dispatch_id = str(row.get("dispatch_id") or "")
    result = signal_park(
        dispatch_id,
        intent_id=None,
        drain_epoch=None,
        actor=actor,
        reason="density-harness-ignored-steer",
    )
    from services.git_integration_worker.cursor_dispatch_ledger import (
        CursorDispatchLedger,
    )

    merge_density_control(
        CursorDispatchLedger.instance(),
        dispatch_id=dispatch_id,
        patch={"density_steer_parked": True},
    )
    return result


def sync_density_steer_delivery(
    row: Mapping[str, Any],
    *,
    tool_call_count: int | None = None,
    spool_dir: Any | None = None,
) -> bool:
    """If spool shows delivery, stamp ledger for ignored-steer enforcement."""
    ctrl = _control_blob(str(row.get("record_json") or ""))
    if ctrl.get("density_steer_delivered"):
        return True
    entry_id = ctrl.get("density_steer_entry_id")
    if not entry_id:
        return False
    dispatch_id = str(row.get("dispatch_id") or "")
    deposit = SteerDepositResult(
        dispatch_id=dispatch_id,
        entry_id=str(entry_id),
        authority_turn_id=str(ctrl.get("density_steer_authority_turn_id") or ""),
        spool_path="",
    )
    if not deposit.authority_turn_id:
        return False
    ack = poll_delivery_ack(deposit, spool_dir=spool_dir)
    if ack is None:
        return False
    reading = read_density_harness_meter(str(row.get("record_json") or ""), model="")
    effective = _effective_tool_call_count(reading, tool_call_count)
    mark_density_steer_delivered(
        dispatch_id=dispatch_id, tool_call_count=effective
    )
    return True


__all__ = [
    "ClaimOutcome",
    "DensityHarnessConfig",
    "DensityHarnessStreamHook",
    "DensityMeterReading",
    "apply_synthetic_density_trajectory",
    "build_density_steer_directive",
    "density_harness_control_patch",
    "density_harness_record_patch",
    "estimate_tokens_from_text",
    "finalize_density_steer_deposit",
    "load_density_harness_config",
    "merge_density_control",
    "merge_density_trajectory",
    "maybe_deposit_density_steer",
    "maybe_park_ignored_density_steer",
    "read_density_harness_meter",
    "release_density_steer_claim",
    "should_steer_density_hop",
    "steer_threshold_tokens",
    "sync_density_steer_delivery",
    "try_claim_density_steer_deposit",
]
