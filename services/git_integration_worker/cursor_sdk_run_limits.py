"""Packet-declared tool-call budget resolution and stop-cause taxonomy."""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

from universal_logging import get_logger

logger = get_logger(__name__)

RUN_LIMITS_KEY = "run_limits"
RUN_BUDGET_CONTROL_KEY = "run_budget_control"
RUN_BUDGET_EXHAUSTED_REASON = "tool-call-budget-exhausted"
UPSTREAM_LIMITS_STAMP: Literal["unknown"] = "unknown"

_TOOL_CALL_BUDGET_RE = re.compile(
    r"^tool_call_budget:\s*(\S+)\s*$", re.IGNORECASE | re.MULTILINE
)
_NATIVE_HELPER_OPT_IN_RE = re.compile(
    r"^native_helper_allow:\s*([^\n]+)\s*$", re.IGNORECASE | re.MULTILINE
)

_DEFAULT_MIN = 1
_DEFAULT_MAX = 2000
_DEFAULT_WARN_FRACTION = 0.85


@dataclass(frozen=True, slots=True)
class RunLimitsBounds:
    floor: int
    ceiling: int


@dataclass(frozen=True, slots=True)
class RunLimits:
    tool_call_budget: int | None
    warn_at: int | None
    upstream_tool_call_limits: Literal["unknown"]
    native_helper_enforcement: str

    def to_record(self) -> dict[str, Any]:
        budget = self.tool_call_budget
        return {
            "tool_call_budget": budget,
            "warn_at": self.warn_at,
            "upstream_tool_call_limits": self.upstream_tool_call_limits,
            "native_helper_enforcement": self.native_helper_enforcement,
        }


def load_run_limits_bounds() -> RunLimitsBounds:
    raw_min = os.environ.get("GIW_TOOL_CALL_BUDGET_MIN", "").strip()
    raw_max = os.environ.get("GIW_TOOL_CALL_BUDGET_MAX", "").strip()
    floor = _DEFAULT_MIN
    ceiling = _DEFAULT_MAX
    if raw_min:
        try:
            floor = int(raw_min)
        except ValueError:
            logger.warning("invalid GIW_TOOL_CALL_BUDGET_MIN=%r", raw_min)
    if raw_max:
        try:
            ceiling = int(raw_max)
        except ValueError:
            logger.warning("invalid GIW_TOOL_CALL_BUDGET_MAX=%r", raw_max)
    if floor < 1:
        floor = _DEFAULT_MIN
    if ceiling < floor:
        ceiling = max(floor, _DEFAULT_MAX)
    return RunLimitsBounds(floor=floor, ceiling=ceiling)


def extract_tool_call_budget_from_packet(text: str) -> str | None:
    """Return the raw front-matter token for ``tool_call_budget:`` when present."""
    if not text:
        return None
    match = _TOOL_CALL_BUDGET_RE.search(text)
    if not match:
        return None
    return match.group(1).strip()


def extract_native_helper_allow_from_packet(text: str) -> tuple[str, ...]:
    match = _NATIVE_HELPER_OPT_IN_RE.search(text or "")
    if not match:
        return ()
    raw = match.group(1).strip()
    if not raw or raw.lower() in {"none", "null", "false", "0"}:
        return ()
    return tuple(part.strip() for part in raw.split(",") if part.strip())


def compute_warn_at(budget: int, *, bounds: RunLimitsBounds | None = None) -> int:
    _ = bounds
    raw_frac = os.environ.get("GIW_TOOL_CALL_WARN_FRACTION", "").strip()
    fraction = _DEFAULT_WARN_FRACTION
    if raw_frac:
        try:
            fraction = float(raw_frac)
        except ValueError:
            logger.warning("invalid GIW_TOOL_CALL_WARN_FRACTION=%r", raw_frac)
    if fraction <= 0 or fraction >= 1:
        fraction = _DEFAULT_WARN_FRACTION
    warn = int(budget * fraction)
    return max(1, min(warn, budget - 1)) if budget > 1 else 1


class RunLimitsRefusal(ValueError):  # noqa: N818
    """Admit-time refusal for out-of-bounds ``tool_call_budget``."""

    def __init__(self, message: str, *, bounds: RunLimitsBounds) -> None:
        super().__init__(message)
        self.bounds = bounds


def resolve_run_limits(
    *,
    packet_text: str = "",
    native_helper_enforcement: str,
) -> RunLimits:
    """Resolve ledger admit payload; default budget unset (behavior-preserving)."""
    bounds = load_run_limits_bounds()
    token = extract_tool_call_budget_from_packet(packet_text)
    budget: int | None = None
    warn_at: int | None = None
    if token is not None:
        lowered = token.lower()
        if lowered not in {"none", "null", "unset", ""}:
            try:
                budget = int(token)
            except ValueError as exc:
                raise RunLimitsRefusal(
                    f"tool_call_budget must be an integer; got {token!r} "
                    f"(allowed range [{bounds.floor}, {bounds.ceiling}])",
                    bounds=bounds,
                ) from exc
            if budget < bounds.floor or budget > bounds.ceiling:
                raise RunLimitsRefusal(
                    f"tool_call_budget={budget} outside "
                    f"[{bounds.floor}, {bounds.ceiling}]",
                    bounds=bounds,
                )
            warn_at = compute_warn_at(budget, bounds=bounds)
    return RunLimits(
        tool_call_budget=budget,
        warn_at=warn_at,
        upstream_tool_call_limits=UPSTREAM_LIMITS_STAMP,
        native_helper_enforcement=native_helper_enforcement,
    )


def undeclared_run_limits(*, native_helper_enforcement: str) -> RunLimits:
    return RunLimits(
        tool_call_budget=None,
        warn_at=None,
        upstream_tool_call_limits=UPSTREAM_LIMITS_STAMP,
        native_helper_enforcement=native_helper_enforcement,
    )


def format_run_limits_preamble_line(limits: RunLimits) -> str:
    budget_label = str(limits.tool_call_budget) if limits.tool_call_budget else "none"
    warn_label = str(limits.warn_at) if limits.warn_at is not None else "none"
    return (
        f"tool_call_budget={budget_label}; warn_at={warn_label}; "
        f"upstream limits {limits.upstream_tool_call_limits}"
    )


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


def read_run_limits(record_json: str | Mapping[str, Any] | None) -> RunLimits | None:
    data = _parse_record_data(record_json)
    blob = data.get(RUN_LIMITS_KEY)
    if not isinstance(blob, dict):
        return None
    budget = blob.get("tool_call_budget")
    parsed_budget: int | None
    if budget is None:
        parsed_budget = None
    else:
        try:
            parsed_budget = int(budget)
        except (TypeError, ValueError):
            parsed_budget = None
    warn = blob.get("warn_at")
    warn_at: int | None
    if warn is None:
        warn_at = None
    else:
        try:
            warn_at = int(warn)
        except (TypeError, ValueError):
            warn_at = None
    enforcement = str(blob.get("native_helper_enforcement") or "unenforced")
    upstream = blob.get("upstream_tool_call_limits")
    upstream_stamp: Literal["unknown"] = (
        "unknown" if str(upstream or "unknown") == "unknown" else "unknown"
    )
    return RunLimits(
        tool_call_budget=parsed_budget,
        warn_at=warn_at,
        upstream_tool_call_limits=upstream_stamp,
        native_helper_enforcement=enforcement,
    )


def run_limits_record_patch(blob: Mapping[str, Any]) -> dict[str, Any]:
    return {RUN_LIMITS_KEY: dict(blob)}


def classify_stop_cause(
    *,
    park_reason: str | None = None,
    run_status: str | None = None,
    budget_exhausted: bool = False,
    executor_voluntary: bool = False,
) -> str:
    if budget_exhausted or park_reason == RUN_BUDGET_EXHAUSTED_REASON:
        return "budget_exhausted"
    if park_reason:
        return f"parked:{park_reason}"
    if run_status and run_status not in ("finished", "completed", "ok", ""):
        return f"run_status:{run_status}"
    if executor_voluntary:
        return "executor_ended"
    return "unknown"


def stored_stop_cause_from_record(record_json: str) -> str | None:
    data = _parse_record_data(record_json)
    ctrl = data.get(RUN_BUDGET_CONTROL_KEY) or {}
    stored = ctrl.get("stop_cause")
    if isinstance(stored, str) and stored:
        return stored
    return None


def resolve_park_harvest_stop_cause(
    *,
    record_json: str,
    park_reason: str | None,
) -> str | None:
    """Serialized stop cause for a park partial sidecar write."""
    stored = stored_stop_cause_from_record(record_json)
    if stored:
        return stored
    data = _parse_record_data(record_json)
    ctrl = data.get(RUN_BUDGET_CONTROL_KEY) or {}
    budget_exhausted = bool(ctrl.get("budget_exhausted"))
    if park_reason == RUN_BUDGET_EXHAUSTED_REASON or budget_exhausted:
        return "budget_exhausted"
    if park_reason:
        return classify_stop_cause(
            park_reason=park_reason,
            budget_exhausted=budget_exhausted,
        )
    return None


def classify_stop_from_closeout_row(row: Mapping[str, Any]) -> str:
    """Prefer a durable stop_cause; replay classifier only when absent."""
    record_json = str(row.get("record_json") or "")
    data = _parse_record_data(record_json)
    ctrl = data.get(RUN_BUDGET_CONTROL_KEY) or {}
    stored = ctrl.get("stop_cause")
    if isinstance(stored, str) and stored:
        return stored
    budget_exhausted = bool(ctrl.get("budget_exhausted"))
    park = row.get("park") if isinstance(row.get("park"), dict) else {}
    park_reason = str(park.get("reason") or "") or None
    run_status = str(row.get("run_status") or row.get("status") or "")
    tool_calls = row.get("tool_call_count") or row.get("stream_tool_call_count")
    voluntary = (
        not budget_exhausted
        and not park_reason
        and run_status in ("finished", "completed", "partial", "unverified", "")
        and tool_calls is not None
    )
    return classify_stop_cause(
        park_reason=park_reason,
        run_status=None if voluntary else run_status,
        budget_exhausted=budget_exhausted,
        executor_voluntary=voluntary,
    )


__all__ = [
    "RUN_BUDGET_CONTROL_KEY",
    "RUN_BUDGET_EXHAUSTED_REASON",
    "RUN_LIMITS_KEY",
    "RunLimits",
    "RunLimitsBounds",
    "RunLimitsRefusal",
    "UPSTREAM_LIMITS_STAMP",
    "classify_stop_cause",
    "classify_stop_from_closeout_row",
    "resolve_park_harvest_stop_cause",
    "stored_stop_cause_from_record",
    "compute_warn_at",
    "extract_native_helper_allow_from_packet",
    "extract_tool_call_budget_from_packet",
    "format_run_limits_preamble_line",
    "load_run_limits_bounds",
    "read_run_limits",
    "resolve_run_limits",
    "run_limits_record_patch",
    "undeclared_run_limits",
]
