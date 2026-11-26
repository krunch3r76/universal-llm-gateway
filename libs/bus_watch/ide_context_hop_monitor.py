"""Periodic CONTEXT_BUDGET hops for :8 Cursor IDE tabs on endpoint subscriptions."""

from __future__ import annotations

import asyncio
import logging
import re
import subprocess
import sys
import threading
import time
from typing import Any

from bus_watch.digest_budget import (
    _utcnow,
    budget_block_for_endpoint_measure,
    effective_policy,
    latch_used_percent_at_first_summarized,
    seat_budget,
)
from bus_watch.endpoint_subscription_actuation import (
    brg_actuation_allowed,
    hop_actuation_policy_enabled,
    maybe_post_hop_request,
    maybe_post_prepare_note,
    pressure_band_allows_request,
)
from bus_watch.endpoint_subscription_join import (
    RegistryClaims,
    apply_holder_h3,
    claim_tab_uuids,
    enumerate_display8_uuid_rows,
    fetch_registry_claims,
    h1_rows_for_scope,
    live_claims_for_scope,
    per_row_actuation_refused,
    resolve_holder_h12,
)
from bus_watch.endpoint_subscription_transfer import maybe_retire_predecessor
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
_HOUSELESS_DISPLAYS = frozenset({"8", ":8"})
_HOP_CHECK_INTERVAL_S = 60.0
_HOP_COOLDOWN_S = 300.0
_HOP_MONITOR_LOOP_CADENCE_S = 20.0

_check_in_flight = threading.Lock()
_registry_cache: RegistryClaims | None = None
_registry_cache_mono: float = 0.0
_REGISTRY_CACHE_TTL_S = 20.0


def _all_cursor_subscription_rows(store: StoreClient) -> list[dict[str, Any]]:
    """All display subscriptions across agent keys (AC1 — not ``agent=cursor`` only)."""
    try:
        payload = store._bus("GET", "/endpoint-subscriptions")
    except Exception:
        logger.exception("ide_context_hop_monitor: subscription list (all) failed")
        return []
    return list(payload.get("subscriptions") or [])


def _registry_for_tick(*, now_mono: float) -> RegistryClaims:
    global _registry_cache, _registry_cache_mono
    if (
        _registry_cache is not None
        and (now_mono - _registry_cache_mono) < _REGISTRY_CACHE_TTL_S
    ):
        return _registry_cache
    reg = fetch_registry_claims()
    _registry_cache = reg
    _registry_cache_mono = now_mono
    return reg


def houseless_uuid_candidates(
    rows: list[dict[str, Any]], scope: str
) -> list[dict[str, Any]]:
    """Compatibility alias — same rows as ``h1_rows_for_scope``."""
    return h1_rows_for_scope(rows, scope)


def row_stale_vs_successor(
    row: dict[str, Any], rows_same_scope: list[dict[str, Any]]
) -> bool:
    """Deprecated id-based staleness — kept for import stability; claim join is authoritative."""
    _ = (row, rows_same_scope)
    return False


def resolve_scope_actuation_refusal(
    candidates: list[dict[str, Any]],
    *,
    rows_same_scope: list[dict[str, Any]],
    claim_tab_uuid_for_scope: Any = None,
) -> tuple[str | None, bool]:
    """Legacy shim — delegates to ``resolve_holder`` when registry is available."""
    scope = ""
    if rows_same_scope:
        scope = str(rows_same_scope[0].get("thread_scope") or "")
    if not scope and candidates:
        scope = str(candidates[0].get("thread_scope") or "")
    registry = (
        RegistryClaims(())
        if claim_tab_uuid_for_scope is None
        else RegistryClaims(())
    )
    if claim_tab_uuid_for_scope is not None:
        # Test shim: build synthetic claim entries from the callable.
        uid = claim_tab_uuid_for_scope(scope)
        if uid:
            registry = RegistryClaims(
                (
                    {
                        "kind": "claim",
                        "status": "claimed",
                        "thread_id": scope,
                        "expires_at": "2099-01-01T00:00:00Z",
                        "address": {"tab_url": f"cursor://tab/{uid}"},
                    },
                )
            )
    from bus_watch.endpoint_subscription_join import resolve_holder

    res = resolve_holder(scope, rows_same_scope or candidates, registry)
    return res.actuation_refused, res.actuation_eligible


def _sample_actuation_eligible(
    resolution_eligible: bool,
    refused: str | None,
) -> bool:
    if refused is not None:
        return False
    return resolution_eligible


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


def _scopes_from_all_rows(all_rows: list[dict[str, Any]]) -> set[str]:
    scopes: set[str] = set()
    for row in enumerate_display8_uuid_rows(all_rows):
        scope = str(row.get("thread_scope") or "").strip()
        if _ROOT_SCOPE_RE.match(scope):
            scopes.add(scope)
    return scopes


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


def _maybe_actuate_holder(
    store: StoreClient,
    *,
    root_id: str,
    holder_row: dict[str, Any],
    budget: dict[str, Any],
    policy: dict[str, Any],
) -> None:
    band = str(budget.get("pressure_band") or "none")
    if band == "prepare":
        maybe_post_prepare_note(
            store,
            scope=root_id,
            holder_row=holder_row,
            prepare_advisory=budget.get("prepare_advisory"),
        )
        return
    if not pressure_band_allows_request(band):
        return
    if not brg_actuation_allowed(root_id, policy):
        return
    maybe_post_hop_request(
        store,
        scope=root_id,
        holder_row=holder_row,
        band=band,
        policy_enabled=hop_actuation_policy_enabled(policy),
        glass_generating=budget.get("glass_status") == "generating",
        as_of=str(budget.get("as_of") or _utcnow()),
        used_tokens=int(budget.get("used_tokens") or 0),
        window_limit_tokens=int(budget.get("window_limit_tokens") or 0),
    )


def _sample_houseless_scope(
    root_id: str,
    subscribed_rows: list[dict[str, Any]],
    all_scope_rows: list[dict[str, Any]],
    *,
    store: StoreClient,
    registry: RegistryClaims,
) -> None:
    policy = _policy_for_root(root_id)
    gui_row = all_scope_rows[0] if all_scope_rows else {}
    gui_host = str(gui_row.get("display") or ":8").strip() or ":8"
    if gui_host == "8":
        gui_host = ":8"
    policy = {**policy, "gui_host": gui_host}
    scope_rows = h1_rows_for_scope(subscribed_rows, root_id)
    claims = live_claims_for_scope(registry, root_id)
    claim_uuids = claim_tab_uuids(claims)
    digest_ts = _utcnow()

    holder_budget: dict[str, Any] | None = None
    holder_h12 = resolve_holder_h12(root_id, scope_rows, registry)
    probes_by_uuid: dict[str, str | None] = {}

    for cand in scope_rows:
        agent_id = str(cand.get("agent_id") or "")
        agent_key = agent_id.lower()
        measure = measure_ide_tab(
            root_id,
            {},
            policy,
            transcript_id=agent_id,
        )
        probe: str | None = None
        if measure is None:
            probe = "incomplete"
        else:
            budget = budget_block_for_endpoint_measure(
                measure, policy=policy, as_of=digest_ts
            )
            probe = budget.get("budget_probe")
            if probe is None:
                probe = "incomplete"
        probes_by_uuid[agent_key] = probe
        if holder_h12.holder_uuid == agent_key:
            holder_final = apply_holder_h3(holder_h12, probe)
        else:
            holder_final = holder_h12
        refused = per_row_actuation_refused(
            cand,
            holder_final,
            claim_uuids=claim_uuids,
            h1_rows=scope_rows,
        )
        eligible = _sample_actuation_eligible(
            holder_final.actuation_eligible and holder_h12.holder_uuid == agent_key,
            refused,
        )
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
                actuation_eligible=eligible,
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
            actuation_eligible=eligible,
            used_percent_at_first_summarized=used_percent_at_first_summarized,
            prepare_advisory=budget.get("prepare_advisory"),
        )
        if (
            holder_h12.holder_uuid == agent_key
            and holder_h12.holder_row is not None
            and apply_holder_h3(holder_h12, probe).actuation_eligible
        ):
            holder_budget = budget

    holder_final = None
    if holder_h12.holder_uuid:
        holder_final = apply_holder_h3(
            holder_h12, probes_by_uuid.get(holder_h12.holder_uuid)
        )

    if holder_final and holder_final.holder_row and holder_budget:
        try:
            _maybe_actuate_holder(
                store,
                root_id=root_id,
                holder_row=holder_final.holder_row,
                budget=holder_budget,
                policy=policy,
            )
            maybe_retire_predecessor(
                store,
                root_id=root_id,
                registry=registry,
                all_rows=subscribed_rows,
            )
        except Exception:
            logger.exception(
                "ide_context_hop_monitor: actuation failed root=%s", root_id
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
        all_rows = _all_cursor_subscription_rows(store)
    except Exception:
        logger.exception("ide_context_hop_monitor: subscription list failed")
        return
    registry = _registry_for_tick(now_mono=mono)
    cursor_rows = store.subscriptions("cursor")
    scopes = _scopes_from_all_rows(all_rows) | {
        str(sub.get("thread_scope") or "").strip()
        for sub in _numeric_root_subscriptions(cursor_rows)
        if _ROOT_SCOPE_RE.match(str(sub.get("thread_scope") or "").strip())
    }
    for root_id in sorted(scopes):
        if not _root_due(root_id, hop_mark, now=mono):
            continue
        hop_mark[f"check:{root_id}"] = mono
        lock = read_lock(root_id)
        scope_all_rows = [
            r
            for r in all_rows
            if str(r.get("thread_scope") or "").strip() == root_id
        ]
        uuid_rows = enumerate_display8_uuid_rows(scope_all_rows)
        sub = next(
            (r for r in cursor_rows if str(r.get("thread_scope") or "") == root_id),
            uuid_rows[0] if uuid_rows else None,
        )
        if not ide_holder_transcript(lock) and uuid_rows:
            try:
                _sample_houseless_scope(
                    root_id,
                    all_rows,
                    scope_all_rows,
                    store=store,
                    registry=registry,
                )
            except Exception:
                logger.exception(
                    "ide_context_hop_monitor: houseless sample failed root=%s",
                    root_id,
                )
            continue
        if sub is None:
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
