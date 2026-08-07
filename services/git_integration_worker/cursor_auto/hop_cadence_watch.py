"""Hop-cadence watch ledger — enroll, age, and evaluate operator CSE seats.

Owned by cursor-auto (not the CDP seat). Persists beside the CDP registry so
watches survive GIW restart. Callers: ``hop_cadence`` fire path and enqueue
observe hook. Prefer registry ``started_at`` when ``active.json`` has the row;
otherwise age from first Auto observe (``seated_at``).
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent_seat.registry import normalize_bus_address
from claude_bundles.cdp_registry_store import load_active
from universal_logging import get_logger

from services.git_integration_worker.cursor_auto.queue import AutoJob

logger = get_logger(__name__)

# §14b: human observed deep at HEARTBEAT #6 (~24 min @ 240s). Interim bind.
_DEFAULT_AGE_THRESHOLD_S = 1500.0
_DEFAULT_COOLDOWN_S = 1800.0
_DEFAULT_SCAN_INTERVAL_S = 30.0
_STANDING_HANDOFF_STALE_FACTOR = 2.0
_MCP_FILES_ROOT = Path("/mnt/torus/mcp-data/files")
_WATCH_FILENAME = "hop_cadence_watches.json"


def age_threshold_s() -> float:
    """Seconds of CSE/watch age before Auto fires a continuity hop.

    Override with env ``CURSOR_AUTO_HOP_CSE_AGE_S`` (minimum 60s).
    """
    raw = os.environ.get("CURSOR_AUTO_HOP_CSE_AGE_S", "").strip()
    if not raw:
        return _DEFAULT_AGE_THRESHOLD_S
    try:
        return max(60.0, float(raw))
    except ValueError:
        return _DEFAULT_AGE_THRESHOLD_S


def cooldown_s() -> float:
    """Seconds after a cadence hop before the same lane may hop again.

    Override with env ``CURSOR_AUTO_HOP_COOLDOWN_S`` (minimum 60s).
    """
    raw = os.environ.get("CURSOR_AUTO_HOP_COOLDOWN_S", "").strip()
    if not raw:
        return _DEFAULT_COOLDOWN_S
    try:
        return max(60.0, float(raw))
    except ValueError:
        return _DEFAULT_COOLDOWN_S


def scan_interval_s() -> float:
    """Background loop sleep between watch evaluations.

    Override with env ``CURSOR_AUTO_HOP_SCAN_S`` (minimum 5s).
    """
    raw = os.environ.get("CURSOR_AUTO_HOP_SCAN_S", "").strip()
    if not raw:
        return _DEFAULT_SCAN_INTERVAL_S
    try:
        return max(5.0, float(raw))
    except ValueError:
        return _DEFAULT_SCAN_INTERVAL_S


def watches_path() -> Path:
    """Durable watch ledger path beside the CDP registry store."""
    return Path.home() / ".gateway" / "cdp-registry" / _WATCH_FILENAME


def standing_handoff_path(thread_id: str) -> Path:
    """On-disk path for the standing-handoff cortex note of a private lane."""
    return (
        _MCP_FILES_ROOT
        / "notes"
        / "system"
        / "threads"
        / f"{thread_id}-standing-handoff.md"
    )


def standing_handoff_uri(thread_id: str) -> str:
    """Share URI the successor must read before trusting wake prose."""
    return f"cortex://notes/system/threads/{thread_id}-standing-handoff.md"


@dataclass(frozen=True)
class StandingHandoffFreshness:
    """Observed freshness of the standing handoff sidecar for one lane."""

    status: str  # current | stale | missing
    uri: str
    mtime_epoch: float | None
    age_s: float | None


@dataclass(frozen=True)
class HopDecision:
    """One evaluate() outcome for a watched private lane."""

    thread_id: str
    action: str  # fire | skip
    reason: str
    age_s: float | None = None
    threshold_s: float | None = None
    signal: str | None = None
    handoff: StandingHandoffFreshness | None = None


def assess_standing_handoff(
    thread_id: str, *, now: float | None = None, stale_after_s: float | None = None
) -> StandingHandoffFreshness:
    """Classify standing-handoff freshness from filesystem mtime (observed only)."""
    uri = standing_handoff_uri(thread_id)
    path = standing_handoff_path(thread_id)
    ts = time.time() if now is None else now
    limit = (
        stale_after_s
        if stale_after_s is not None
        else age_threshold_s() * _STANDING_HANDOFF_STALE_FACTOR
    )
    if not path.is_file():
        return StandingHandoffFreshness("missing", uri, None, None)
    mtime = path.stat().st_mtime
    age = max(0.0, ts - mtime)
    status = "stale" if age > limit else "current"
    return StandingHandoffFreshness(status, uri, mtime, age)


def load_watches(path: Path | None = None) -> dict[str, dict[str, Any]]:
    """Load the hop-cadence watch ledger; empty dict on missing/corrupt file."""
    target = path or watches_path()
    if not target.is_file():
        return {}
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("hop_cadence watch load failed path=%s err=%s", target, exc)
        return {}
    if not isinstance(raw, dict):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for key, row in raw.items():
        if isinstance(row, dict):
            out[str(key)] = dict(row)
    return out


def save_watches(watches: dict[str, dict[str, Any]], path: Path | None = None) -> None:
    """Atomically persist the watch ledger (tmp + replace)."""
    target = path or watches_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".tmp")
    tmp.write_text(json.dumps(watches, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(target)


def _is_web_mailbox(from_agent: str) -> bool:
    addr = normalize_bus_address((from_agent or "").strip())
    return addr.startswith("web-")


def should_observe_job(job: AutoJob) -> bool:
    """True when an inbound Auto job should enroll/refresh a hop watch."""
    if job.continuity_hop:
        return False
    if (job.continuity_matched_token or "") == "cadence:auto":
        return False
    subject = (job.subject or "").lower()
    if "hop cadence" in subject and "cursor-auto" in subject:
        return False
    return _is_web_mailbox(job.from_agent)


def registry_started_at(registration_id: str | None) -> float | None:
    """Return ``active.json`` started_at for a live registration, else None."""
    rid = (registration_id or "").strip()
    if not rid:
        return None
    active = load_active()
    row = active.get(rid)
    if not isinstance(row, dict):
        return None
    if row.get("status") not in ("active", "orphaned_alive", "allocating"):
        return None
    started = row.get("started_at")
    if started is None:
        return None
    try:
        return float(started)
    except (TypeError, ValueError):
        return None


def observe_lane_from_enqueue(
    job: AutoJob, *, now: float | None = None, path: Path | None = None
) -> dict[str, Any] | None:
    """Enroll or refresh a hop watch from a web-* Auto enqueue (writes disk)."""
    if not should_observe_job(job):
        return None
    ts = time.time() if now is None else now
    watches = load_watches(path)
    thread_id = str(job.thread_id)
    row = dict(watches.get(thread_id) or {})
    seated = row.get("seated_at")
    if seated is None:
        reg_started = registry_started_at(job.cse_registration_id)
        row["seated_at"] = float(reg_started) if reg_started is not None else ts
        row["enroll_source"] = (
            "registry_started_at" if reg_started is not None else "first_auto_observe"
        )
    row["thread_id"] = thread_id
    row["last_seen_at"] = ts
    row["from_agent"] = normalize_bus_address(job.from_agent)
    if job.cse_registration_id:
        row["registration_id"] = job.cse_registration_id
    # Bus thread watch ≠ registry Chrome host. Prefer job-supplied session
    # address; else join from registry row (bind_session_address at birth).
    chat_url = (job.cse_chat_url or "").strip() or None
    if not chat_url and job.cse_registration_id:
        from claude_bundles.cdp_registry import chat_url_for_registration

        chat_url = chat_url_for_registration(job.cse_registration_id)
    if chat_url:
        row["chat_url"] = chat_url
    row["purpose"] = "operator-proxy"
    watches[thread_id] = row
    save_watches(watches, path)
    logger.info(
        "hop_cadence observe thread=%s seated_at=%s age_s=%.1f",
        thread_id,
        row.get("seated_at"),
        ts - float(row["seated_at"]),
    )
    return row


def effective_seated_at(row: dict[str, Any]) -> float | None:
    """Prefer live registry started_at for the row; else watch seated_at."""
    reg_started = registry_started_at(str(row.get("registration_id") or "") or None)
    if reg_started is not None:
        return reg_started
    seated = row.get("seated_at")
    if seated is None:
        return None
    try:
        return float(seated)
    except (TypeError, ValueError):
        return None


def evaluate_watch(
    row: dict[str, Any],
    *,
    now: float | None = None,
    threshold: float | None = None,
    cool: float | None = None,
) -> HopDecision:
    """Decide fire/skip for one watch row; pure — does not mutate the ledger."""
    ts = time.time() if now is None else now
    thr = age_threshold_s() if threshold is None else threshold
    cd = cooldown_s() if cool is None else cool
    thread_id = str(row.get("thread_id") or "")
    if not thread_id:
        return HopDecision("", "skip", "missing_thread_id")
    last_hop = row.get("last_hop_at")
    if last_hop is not None:
        try:
            if ts - float(last_hop) < cd:
                return HopDecision(
                    thread_id, "skip", "cooldown", threshold_s=thr, signal="cooldown"
                )
        except (TypeError, ValueError):
            pass
    seated = effective_seated_at(row)
    if seated is None:
        return HopDecision(thread_id, "skip", "no_seated_at", threshold_s=thr)
    age = max(0.0, ts - seated)
    signal = (
        "registry_started_at"
        if registry_started_at(str(row.get("registration_id") or "") or None) is not None
        else "watch_seated_at"
    )
    handoff = assess_standing_handoff(thread_id, now=ts)
    if age < thr:
        return HopDecision(
            thread_id,
            "skip",
            "below_threshold",
            age_s=age,
            threshold_s=thr,
            signal=signal,
            handoff=handoff,
        )
    return HopDecision(
        thread_id,
        "fire",
        "age_threshold_met",
        age_s=age,
        threshold_s=thr,
        signal=signal,
        handoff=handoff,
    )


def mark_hop_fired(
    thread_id: str,
    *,
    now: float | None = None,
    path: Path | None = None,
    execution_id: str | None = None,
) -> None:
    """Reset seated_at after a cadence hop so the successor is not immediately re-hopped."""
    ts = time.time() if now is None else now
    watches = load_watches(path)
    row = dict(watches.get(thread_id) or {"thread_id": thread_id})
    row["thread_id"] = thread_id
    row["last_hop_at"] = ts
    row["seated_at"] = ts
    row["enroll_source"] = "post_hop_reset"
    if execution_id:
        row["last_hop_execution_id"] = execution_id
    watches[thread_id] = row
    save_watches(watches, path)
