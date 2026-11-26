"""Periodic CONTEXT_BUDGET hops for :8 Cursor IDE tabs on endpoint subscriptions."""

from __future__ import annotations

import asyncio
import logging
import re
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from typing import Any

from bus_watch.digest_budget import (
    _utcnow,
    budget_block_for_endpoint_measure,
    effective_policy,
    latch_used_percent_at_first_summarized,
    seat_budget,
)
from bus_watch.endpoint_wake_transport import StoreClient
from bus_watch.fable_lock import WATCH_DIR, bus_watch_repo_root, read_lock
from bus_watch.ide_budget import (
    active_budget_excludes,
    ide_holder_transcript,
    measure_ide_tab,
)
from bus_watch.tick_state import load_state

logger = logging.getLogger(__name__)

_REPO = bus_watch_repo_root()
_HOP_SCRIPT = _REPO / "scripts" / "liaison-ide-hop.py"
_ROOT_SCOPE_RE = re.compile(r"^\d+$")
_UUID_AGENT_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.I,
)
_GI_TRIAL_STAMP = "cursor-grand-impresario"
_HOUSELESS_DISPLAYS = frozenset({"8", ":8"})
_HOP_CHECK_INTERVAL_S = 60.0
_HOP_COOLDOWN_S = 300.0
# Must stay below _HOP_CHECK_INTERVAL_S so a root due at 60s is seen on the next tick, not +60s later.
_HOP_MONITOR_LOOP_CADENCE_S = 20.0

_check_in_flight = threading.Lock()


def _uuid_agent_id(value: object) -> str | None:
    text = str(value or "").strip()
    return text if _UUID_AGENT_RE.match(text) else None


def _all_cursor_subscription_rows(store: StoreClient) -> list[dict[str, Any]]:
    try:
        payload = store._bus("GET", "/endpoint-subscriptions", params={"agent": "cursor"})
    except Exception:
        logger.exception("ide_context_hop_monitor: subscription list (all) failed")
        return []
    return list(payload.get("subscriptions") or [])


def houseless_uuid_candidates(
    rows: list[dict[str, Any]], scope: str
) -> list[dict[str, Any]]:
    """Subscribed :8 cursor rows with UUID-shaped ``agent_id`` for one numeric scope."""
    out: list[dict[str, Any]] = []
    scope_s = str(scope or "").strip()
    for row in rows:
        if str(row.get("thread_scope") or "").strip() != scope_s:
            continue
        if str(row.get("state") or "") != "subscribed":
            continue
        disp = str(row.get("display") or "").strip()
        if disp not in _HOUSELESS_DISPLAYS:
            continue
        if _uuid_agent_id(row.get("agent_id")) is None:
            continue
        out.append(row)
    return out


def row_stale_vs_successor(
    row: dict[str, Any], rows_same_scope: list[dict[str, Any]]
) -> bool:
    """True when a newer subscription row supersedes this row on the same scope."""
    row_id = int(row.get("id") or 0)
    for other in rows_same_scope:
        other_id = int(other.get("id") or 0)
        if other_id > row_id and str(other.get("state") or "") == "subscribed":
            return True
    return False


def _stamp_claim_eligible_rows(
    candidates: list[dict[str, Any]],
    *,
    claim_tab_uuid_for_scope: Callable[[str], str | None] | None,
) -> list[dict[str, Any]]:
    """GI-stamped rows whose ``agent_id`` matches the live scope claim (ignores stale)."""
    if claim_tab_uuid_for_scope is None:
        return []
    out: list[dict[str, Any]] = []
    for row in candidates:
        if str(row.get("bound_by") or "") != _GI_TRIAL_STAMP:
            continue
        scope = str(row.get("thread_scope") or "")
        agent_id = str(row.get("agent_id") or "")
        claimed = claim_tab_uuid_for_scope(scope)
        if claimed and claimed == agent_id:
            out.append(row)
    return out


def resolve_scope_actuation_refusal(
    candidates: list[dict[str, Any]],
    *,
    rows_same_scope: list[dict[str, Any]],
    claim_tab_uuid_for_scope: Callable[[str], str | None] | None = None,
) -> tuple[str | None, bool]:
    """Scope actuation gate for S2 — emits ``actuation_refused`` labels in S1."""
    if not candidates:
        return "unknown", False
    if claim_tab_uuid_for_scope is None:
        return "no_live_claim", False
    stamp_claim_rows = _stamp_claim_eligible_rows(
        candidates, claim_tab_uuid_for_scope=claim_tab_uuid_for_scope
    )
    if len(stamp_claim_rows) > 1:
        return "ambiguous", False
    stamped_rows = [
        row
        for row in candidates
        if str(row.get("bound_by") or "") == _GI_TRIAL_STAMP
    ]
    if not stamped_rows:
        return "no_live_claim", False
    if len(stamp_claim_rows) != 1:
        return "stale", False
    stamp_row = stamp_claim_rows[0]
    if row_stale_vs_successor(stamp_row, rows_same_scope):
        return "stale", False
    return None, True


def _sample_actuation_eligible(
    *,
    scope_eligible: bool,
    refused: str | None,
    budget_probe: str | None,
) -> bool:
    """Actuation-ready only with a live glass CDP probe and no scope refusal."""
    if not scope_eligible or refused is not None:
        return False
    return budget_probe == "glass.cdp"


def _numeric_root_subscriptions(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows:
        if str(row.get("surface") or "") != "cursor":
            continue
        scope = str(row.get("thread_scope") or "").strip()
        if not _ROOT_SCOPE_RE.match(scope):
            continue
        disp = str(row.get("display") or "").strip()
        if disp not in ("8", ":8", "orion-node"):
            continue
        out.append(row)
    return out


def _cooldown_key(root_id: str, transcript_id: str) -> str:
    return f"hop:{root_id}:{transcript_id}"


def _root_due(root_id: str, mark: dict[str, float], *, now: float) -> bool:
    last = mark.get(f"check:{root_id}", 0.0)
    return (now - last) >= _HOP_CHECK_INTERVAL_S


def _transcript_cooled(
    root_id: str, transcript_id: str, mark: dict[str, float], *, now: float
) -> bool:
    last = mark.get(_cooldown_key(root_id, transcript_id), 0.0)
    return (now - last) >= _HOP_COOLDOWN_S


def _policy_for_root(root_id: str) -> dict[str, Any]:
    state_path = WATCH_DIR / f"liaison-{root_id}.tick.json"
    state = load_state(state_path)
    return effective_policy(state)


def _sample_houseless_scope(
    root_id: str,
    row: dict[str, Any],
    all_scope_rows: list[dict[str, Any]],
    subscribed_rows: list[dict[str, Any]],
    *,
    claim_tab_uuid_for_scope: Callable[[str], str | None] | None = None,
) -> None:
    policy = _policy_for_root(root_id)
    gui_host = str(row.get("display") or ":8").strip() or ":8"
    if gui_host == "8":
        gui_host = ":8"
    policy = {**policy, "gui_host": gui_host}
    candidates = houseless_uuid_candidates(subscribed_rows, root_id)
    scope_refusal, scope_eligible = resolve_scope_actuation_refusal(
        candidates,
        rows_same_scope=all_scope_rows,
        claim_tab_uuid_for_scope=claim_tab_uuid_for_scope,
    )
    digest_ts = _utcnow()
    for cand in candidates:
        agent_id = str(cand.get("agent_id") or "")
        measure = measure_ide_tab(
            root_id,
            {},
            policy,
            transcript_id=agent_id,
        )
        refused = scope_refusal
        if row_stale_vs_successor(cand, all_scope_rows):
            refused = "stale"
        if measure is None:
            safe_emit_ide_tab_budget_sampled(
                root_id=root_id,
                transcript_id=agent_id,
                tool_calls=0,
                tool_result_tokens=None,
                used_tokens=0,
                window_limit_tokens=int(policy.get("ide_window_tokens") or 256_000),
                stop_class=None,
                hop_weight_basis=None,
                matched_categories=[],
                subscription_id=cand.get("id"),
                holder_basis="endpoint_subscription",
                budget_probe="incomplete",
                glass_status=None,
                as_of=digest_ts,
                pressure_band="none",
                actuation_refused=refused,
                actuation_eligible=_sample_actuation_eligible(
                    scope_eligible=scope_eligible,
                    refused=refused,
                    budget_probe="incomplete",
                ),
            )
            continue
        budget = budget_block_for_endpoint_measure(
            measure, policy=policy, as_of=digest_ts
        )
        glass_summarized = int(
            budget.get("glass_summarized_tokens")
            or measure.get("glass_summarized_tokens")
            or 0
        )
        used_percent_at_first_summarized = latch_used_percent_at_first_summarized(
            subscription_id=cand.get("id"),
            transcript_id=agent_id,
            glass_summarized_tokens=glass_summarized,
            computed_percent=budget.get("used_percent_at_first_summarized"),
        )
        safe_emit_ide_tab_budget_sampled(
            root_id=root_id,
            transcript_id=agent_id,
            tool_calls=int(budget.get("tool_calls") or measure.get("tool_calls") or 0),
            tool_result_tokens=budget.get("tool_result_tokens"),
            used_tokens=int(budget.get("used_tokens") or 0),
            window_limit_tokens=budget.get("window_limit_tokens"),
            stop_class=budget.get("stop_class"),
            hop_weight_basis=budget.get("hop_weight_basis"),
            matched_categories=list(budget.get("matched_categories") or []),
            subscription_id=cand.get("id"),
            holder_basis="endpoint_subscription",
            budget_probe=budget.get("budget_probe"),
            glass_status=budget.get("glass_status"),
            as_of=budget.get("as_of") or digest_ts,
            pressure_band=budget.get("pressure_band"),
            actuation_refused=refused,
            actuation_eligible=_sample_actuation_eligible(
                scope_eligible=scope_eligible,
                refused=refused,
                budget_probe=budget.get("budget_probe"),
            ),
            used_percent_at_first_summarized=used_percent_at_first_summarized,
            prepare_advisory=budget.get("prepare_advisory"),
        )


def _budget_for_root(root_id: str) -> dict[str, Any] | None:
    state_path = WATCH_DIR / f"liaison-{root_id}.tick.json"
    state = load_state(state_path)
    policy = effective_policy(state)
    lock = read_lock(root_id)
    register = str(state.get("register") or "attended")
    measure = measure_ide_tab(root_id, lock, policy)
    if not measure:
        return None
    budget, _pct = seat_budget(
        est=0,
        successor_model=str(policy.get("successor_model") or policy.get("ide_model") or ""),
        usage_live=None,
        holder_dispatch="",
        register=register,
        lock=lock,
        policy=policy,
        root_id=root_id,
        digest_ts=_utcnow(),
        epoch=str(measure.get("transcript_id") or ""),
    )
    return budget


def _run_ide_hop(
    root_id: str, transcript_id: str, *, gui_host: str = ":8"
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(_HOP_SCRIPT),
            "--root",
            root_id,
            "--transcript-id",
            transcript_id,
            "--gui-host",
            gui_host,
            "--force",
        ],
        cwd=str(_REPO),
        timeout=180,
        capture_output=True,
        text=True,
    )


def check_cursor_ide_context_hops(
    store: StoreClient,
    *,
    mark: dict[str, float] | None = None,
    now: float | None = None,
) -> None:
    """Every 60s per root, hop idle :8 Cursor tabs that hit CONTEXT_BUDGET."""
    hop_mark = mark if mark is not None else {}
    mono = time.monotonic() if now is None else now
    try:
        rows = store.subscriptions("cursor")
    except Exception:
        logger.exception("ide_context_hop_monitor: subscription list failed")
        return
    all_rows = _all_cursor_subscription_rows(store)
    for sub in _numeric_root_subscriptions(rows):
        root_id = str(sub.get("thread_scope") or "")
        if not _root_due(root_id, hop_mark, now=mono):
            continue
        hop_mark[f"check:{root_id}"] = mono
        lock = read_lock(root_id)
        scope_sub_rows = [
            r
            for r in rows
            if str(r.get("thread_scope") or "").strip() == root_id
        ]
        scope_all_rows = [
            r
            for r in all_rows
            if str(r.get("thread_scope") or "").strip() == root_id
        ]
        uuid_candidates = houseless_uuid_candidates(scope_sub_rows, root_id)
        if not ide_holder_transcript(lock) and uuid_candidates:
            try:
                _sample_houseless_scope(
                    root_id,
                    sub,
                    scope_all_rows,
                    rows,
                )
            except Exception:
                logger.exception(
                    "ide_context_hop_monitor: houseless sample failed root=%s",
                    root_id,
                )
            continue
        try:
            budget = _budget_for_root(root_id)
        except Exception:
            logger.exception(
                "ide_context_hop_monitor: budget failed root=%s", root_id
            )
            continue
        if budget:
            safe_emit_ide_tab_budget_sampled(
                root_id=root_id,
                transcript_id=str(budget.get("transcript_id") or ""),
                tool_calls=int(budget.get("tool_calls") or 0),
                tool_result_tokens=budget.get("tool_result_tokens"),
                used_tokens=int(budget.get("used_tokens") or 0),
                window_limit_tokens=budget.get("window_limit_tokens"),
                stop_class=budget.get("stop_class"),
                hop_weight_basis=budget.get("hop_weight_basis"),
                matched_categories=list(budget.get("matched_categories") or []),
            )
        if not budget or budget.get("stop_class") != "CONTEXT_BUDGET":
            continue
        if budget.get("glass_status") == "generating":
            continue
        transcript_id = str(budget.get("transcript_id") or "")
        if not transcript_id:
            continue
        excluded = active_budget_excludes(root_id)
        if transcript_id in excluded:
            continue
        if not _transcript_cooled(root_id, transcript_id, hop_mark, now=mono):
            continue
        hop_mark[_cooldown_key(root_id, transcript_id)] = mono
        sub_display = str(sub.get("display") or ":8").strip() or ":8"
        gui_host = sub_display if sub_display.startswith(":") else sub_display
        if gui_host == "8":
            gui_host = ":8"
        try:
            proc = _run_ide_hop(root_id, transcript_id, gui_host=gui_host)
        except subprocess.TimeoutExpired:
            logger.warning(
                "ide_context_hop_monitor: hop timeout root=%s transcript=%s",
                root_id,
                transcript_id,
            )
            continue
        except Exception:
            logger.exception(
                "ide_context_hop_monitor: hop spawn failed root=%s", root_id
            )
            continue
        if proc.returncode == 0:
            logger.info(
                "ide_context_hop_monitor: hop landed root=%s transcript=%s",
                root_id,
                transcript_id,
            )
        else:
            logger.warning(
                "ide_context_hop_monitor: hop exit=%s root=%s transcript=%s stderr=%s",
                proc.returncode,
                root_id,
                transcript_id,
                (proc.stderr or proc.stdout or "")[:400],
            )


def _guarded_hop_check(
    store: StoreClient, hop_mark: dict[str, float]
) -> tuple[str, float]:
    """Run one hop check if the previous invocation finished; else skip."""
    if not _check_in_flight.acquire(blocking=False):
        return "skipped_overlap", 0.0
    started = time.monotonic()
    outcome = "ok"
    try:
        check_cursor_ide_context_hops(store, mark=hop_mark)
    except Exception:
        outcome = "error"
        logger.exception("ide_context_hop_monitor: guarded check failed")
    finally:
        _check_in_flight.release()
    duration_ms = (time.monotonic() - started) * 1000.0
    return outcome, duration_ms


def safe_emit_hop_monitor_tick(*, outcome: str, check_ms: float) -> None:
    """Emit hop-monitor timing; never raises into the monitor loop."""
    try:
        from bus_watch.events import emit_ide_context_hop_monitor_tick

        emit_ide_context_hop_monitor_tick(outcome=outcome, check_ms=round(check_ms, 3))
    except Exception:
        logger.exception("ide_context_hop_monitor: tick emit failed")


def safe_emit_ide_tab_budget_sampled(**kwargs: Any) -> None:
    """Emit per-tab budget sample; never raises into the hop check."""
    try:
        from bus_watch.events import emit_ide_tab_budget_sampled

        emit_ide_tab_budget_sampled(**kwargs)
    except Exception:
        logger.exception("ide_context_hop_monitor: tab budget sample emit failed")


async def run_ide_context_hop_monitor() -> None:
    """Background loop: CONTEXT_BUDGET hop checks on their own cadence."""
    store = StoreClient()
    hop_mark: dict[str, float] = {}
    while True:
        tick_started = time.monotonic()
        outcome, check_ms = await asyncio.to_thread(
            _guarded_hop_check, store, hop_mark
        )
        safe_emit_hop_monitor_tick(outcome=outcome, check_ms=check_ms)
        elapsed = time.monotonic() - tick_started
        await asyncio.sleep(max(0.0, _HOP_MONITOR_LOOP_CADENCE_S - elapsed))


__all__ = [
    "check_cursor_ide_context_hops",
    "houseless_uuid_candidates",
    "resolve_scope_actuation_refusal",
    "row_stale_vs_successor",
    "run_ide_context_hop_monitor",
    "safe_emit_hop_monitor_tick",
    "safe_emit_ide_tab_budget_sampled",
]
