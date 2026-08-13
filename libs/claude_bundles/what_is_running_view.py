"""Compose ontology-keyed fleet occupancy from active-work + registry + CSR.

Pure join/render helpers for ``scripts/cortex/what_is_running.py``. Callers supply
already-fetched ``active_work`` / registry / sessions dicts — this module does not
probe the network. Invariants: label streams vs attachments vs registrations
separately; never treat ``effective_count`` as admission; flag ≥2 operator-purpose
streams as OVERLAP (succession collision candidate).
"""

from __future__ import annotations

import json
import time
from collections import Counter
from datetime import UTC, datetime
from typing import Any

SCHEMA = "what-is-running/v1"
SNAPSHOT_URI = "cortex://notes/system/operational/what-is-running.json"
OPERATOR_PURPOSES = frozenset({"operator-proxy", "mission"})
# Manual publish is deferred (todo:fleet-what-is-running); 5m TTL stops a forgotten
# snapshot from vouching for liveness longer than one operational read cycle.
DEFAULT_LIVENESS_TTL_S = 300.0
HONEST_EMPTY_SESSIONS = "no live sessions asserted"


def now_iso() -> str:
    """UTC timestamp with trailing Z for operational snapshots."""
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def ts_to_iso_z(ts: float) -> str:
    """Convert unix seconds to operational snapshot ISO-Z."""
    return datetime.fromtimestamp(ts, UTC).isoformat().replace("+00:00", "Z")


def iso_z_to_ts(iso: str | None) -> float | None:
    """Parse operational ISO-Z to unix seconds; None when unparseable."""
    if not iso or not isinstance(iso, str):
        return None
    try:
        text = iso[:-1] + "+00:00" if iso.endswith("Z") else iso
        return datetime.fromisoformat(text).timestamp()
    except (ValueError, TypeError):
        return None


def is_expired(expires_at_utc: str | None, now: float) -> bool:
    """True when ``expires_at_utc`` is missing, unparseable, or not after ``now``."""
    ts = iso_z_to_ts(expires_at_utc)
    if ts is None:
        return True
    return now >= ts


def _obligation_marker() -> dict[str, Any]:
    return {"expiring": False, "obligation": True}


def _honest_empty_scalars(scalars: dict[str, Any]) -> dict[str, Any]:
    """Zero liveness scalars when the snapshot can no longer vouch for occupancy."""
    out = dict(scalars)
    out["streams_running_count"] = 0
    out["attachments_live_cse_count"] = 0
    out["registry_capacity_count"] = 0
    out["effective_count_drain_only"] = 0
    out["at_soft_limit"] = False
    out["at_hard_limit"] = False
    return out


def _recompute_scalars(
    view: dict[str, Any], running: list[dict[str, Any]]
) -> dict[str, Any]:
    """Refresh stream count from filtered ``running`` rows; keep other scalars."""
    scalars = dict(view.get("scalars_actual") or {})
    scalars["streams_running_count"] = len(running)
    return scalars


def age_s(started_at: Any, now: float) -> float | None:
    """Seconds since registry ``started_at``, or None when the field is missing."""
    if not isinstance(started_at, (int, float)):
        return None
    return max(0.0, now - float(started_at))


def lane_for_registration(
    sessions: dict[str, dict[str, Any]], registration_id: str | None
) -> str | None:
    """Return CSR ``lane_thread`` for a registration_id when the hub join hits."""
    if not registration_id:
        return None
    for row in sessions.values():
        ids = row.get("ids") or {}
        if ids.get("registration_id") == registration_id or row.get("cse_id") == (
            registration_id
        ):
            lane = ids.get("lane_thread")
            return str(lane) if lane else None
    return None


def kind_for(purpose: str | None, holder: str | None) -> str:
    """Map purpose/holder into a coarse seat kind for the occupancy table."""
    p = (purpose or "").strip() or "unspecified"
    if p in {"operator-proxy", "mission"}:
        return "operator_seat"
    if p in {"probe", "bounded-subseat"}:
        return p
    if (holder or "").startswith("verify"):
        return "probe"
    return f"seat:{p}"


def compose_view(
    *,
    active_work: dict[str, Any],
    registry: dict[str, dict[str, Any]],
    sessions: dict[str, dict[str, Any]],
    sources: dict[str, str],
    hop_watches: dict[str, dict[str, Any]] | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    """Join stream rows + registry + CSR into an ontology-labeled occupancy view.

    Returns a JSON-serializable dict with ``running`` rows, ``scalars_actual``,
    ``intended`` rules, and ``findings`` (OVERLAP / HYGIENE_DRIFT / ALIGNED).
    Side effects: none. ``hop_watches`` is accepted for caller compat; lane
    join is ``parent_thread`` then CSR ``lane_thread`` (no timestamp guess).
    """
    now = time.time() if now is None else now
    _ = hop_watches
    rows_out: list[dict[str, Any]] = []
    for row in active_work.get("rows") or []:
        if not isinstance(row, dict):
            continue
        rid = row.get("registration_id")
        reg = registry.get(str(rid), {}) if rid else {}
        purpose = row.get("purpose") or reg.get("purpose")
        holder = row.get("holder") or reg.get("holder")
        purpose_s = purpose if isinstance(purpose, str) else None
        holder_s = holder if isinstance(holder, str) else None
        rid_s = str(rid) if rid else None
        lane = (
            (str(row.get("parent_thread")).strip() if row.get("parent_thread") else None)
            or (str(reg.get("parent_thread")).strip() if reg.get("parent_thread") else None)
            or lane_for_registration(sessions, rid_s)
        )
        row_observed = ts_to_iso_z(now)
        row_expires = ts_to_iso_z(now + DEFAULT_LIVENESS_TTL_S)
        rows_out.append(
            {
                "execution_id": row.get("execution_id"),
                "kind": kind_for(purpose_s, holder_s),
                "purpose": purpose,
                "lane": lane,
                "session_url": reg.get("chat_url"),
                "registration_id": rid,
                "registration_status": reg.get("status"),
                "port": reg.get("port"),
                "holder": holder,
                "commissioned_by": holder,
                "age_s": age_s(reg.get("started_at"), now),
                "stream_status": row.get("status"),
                "observed_at_utc": row_observed,
                "expires_at_utc": row_expires,
                "expiring": True,
            }
        )

    status_counts = Counter(
        str(r.get("status") or "?") for r in registry.values() if isinstance(r, dict)
    )
    active_regs = [
        r
        for r in registry.values()
        if isinstance(r, dict) and r.get("status") == "active"
    ]
    op_streams = [
        r for r in rows_out if (r.get("purpose") or "") in OPERATOR_PURPOSES
    ]
    lanes_with_ops: dict[str, list[str]] = {}
    for r in op_streams:
        lane = r.get("lane") or "lane_unknown"
        lanes_with_ops.setdefault(str(lane), []).append(str(r.get("execution_id")))

    findings: list[dict[str, Any]] = []
    intended = {
        "max_operator_proxy_streams_per_lane": 1,
        "stream_soft_limit": active_work.get("soft_limit"),
        "stream_hard_limit": active_work.get("hard_limit"),
        "attachments_are_hygiene_not_admission": True,
        **_obligation_marker(),
    }

    unknown_bucket = lanes_with_ops.get("lane_unknown") or []
    for lane, execs in sorted(lanes_with_ops.items()):
        if lane == "lane_unknown":
            continue
        if len(execs) > 1:
            findings.append(
                {
                    "verdict": "OVERLAP",
                    "rule": "one_operator_seat_per_lane",
                    "lane": lane,
                    "execution_ids": execs,
                    "detail": (
                        f"≥2 operator-proxy/mission streams on lane {lane} — "
                        "succession collision (predecessor not stood down)"
                    ),
                    **_obligation_marker(),
                }
            )

    if len(unknown_bucket) >= 2:
        findings.append(
            {
                "verdict": "OVERLAP_UNVERIFIED",
                "rule": "one_operator_seat_per_lane",
                "lane": "lane_unknown",
                "execution_ids": unknown_bucket,
                "detail": (
                    "≥2 operator-purpose streams with unresolved lane join — "
                    "cannot confirm per-lane exclusivity"
                ),
                **_obligation_marker(),
            }
        )
    elif (
        len(op_streams) >= 2
        and not any(f["verdict"] == "OVERLAP" for f in findings)
        and not unknown_bucket
    ):
        findings.append(
            {
                "verdict": "MULTI_LANE_OK",
                "rule": "one_operator_seat_per_lane",
                "lanes": sorted(
                    {str(r.get("lane")) for r in op_streams if r.get("lane")}
                ),
                "execution_ids": [r.get("execution_id") for r in op_streams],
                "detail": (
                    f"{len(op_streams)} operator-purpose streams on distinct "
                    "lanes — per-lane exclusivity holds; fleet at soft capacity"
                ),
                **_obligation_marker(),
            }
        )

    live_cse = int(active_work.get("live_cse_count") or 0)
    if live_cse > len(active_regs):
        findings.append(
            {
                "verdict": "HYGIENE_DRIFT",
                "rule": "attachments_disposable_once_url_recorded",
                "attachments": live_cse,
                "active_registrations": len(active_regs),
                "detail": (
                    f"{live_cse} open CSE attachments vs {len(active_regs)} active "
                    "registrations — orphaned Chrome tabs inflate live_cse_count; "
                    "attachments ≠ product chats on claude.ai "
                    "(reclaim: todo:cdp-ask-ghost-live-cse-count)"
                ),
                **_obligation_marker(),
            }
        )

    if not findings:
        findings.append(
            {
                "verdict": "ALIGNED",
                "rule": "observed_matches_intended_minimum",
                "detail": "No overlap or attachment-hygiene drift detected",
                **_obligation_marker(),
            }
        )

    running = int(active_work.get("running_count") or 0)
    observed_at_utc = ts_to_iso_z(now)
    expires_at_utc = ts_to_iso_z(now + DEFAULT_LIVENESS_TTL_S)
    return {
        "schema": SCHEMA,
        "snapshot_uri": SNAPSHOT_URI,
        "observed_at_utc": observed_at_utc,
        "expires_at_utc": expires_at_utc,
        "sources": sources,
        "ontology": {
            "session": "URL-addressed CSE (durable, free)",
            "attachment": (
                "Chrome target / open CSE page (ephemeral, scarce) — "
                "NOT product-chat count on claude.ai UI "
                "(specimen 2026-08-07: 11 attachments vs 2 product chats; "
                "agent-bus:6899)"
            ),
            "lane": "agent-bus thread (durable)",
            "seat": "model instance on a lane (holder+purpose)",
            "registration": "time-bounded host bind (active/retained/orphaned_*)",
        },
        "scalars_actual": {
            "streams_running_count": running,
            "streams_running_count_noun": "stream",
            "attachments_live_cse_count": live_cse,
            "attachments_live_cse_count_noun": "attachment",
            "registry_capacity_count": active_work.get("registry_capacity_count"),
            "registry_capacity_count_noun": "registration_host",
            "effective_count_drain_only": active_work.get("effective_count"),
            "at_soft_limit": active_work.get("at_soft_limit"),
            "at_hard_limit": active_work.get("at_hard_limit"),
            "free_slots": active_work.get("free_slots"),
        },
        "registry_status_counts": dict(status_counts),
        "intended": intended,
        "running": rows_out,
        "findings": findings,
        "life_reachability": {
            "live_probe_verbs_on_life": (
                "forbidden (manage/observability/project_ask = code)"
            ),
            "life_path": (
                f"fs(op=read, path={SNAPSHOT_URI!r}) after script --publish "
                "(memoized; not a live probe)"
            ),
            "code_path": "script | manage busy_status | project_ask active_work",
        },
    }


def serve_view(view: dict[str, Any], now: float | None = None) -> dict[str, Any]:
    """Apply liveness expiry on read — the sole filter for ``running`` rows.

    Snapshot past ``expires_at_utc`` ⇒ honest-empty liveness (AC2) while
    obligation blocks (``intended``, ``findings``) pass through unchanged.
    Side effects: none.
    """
    now = time.time() if now is None else now
    out = dict(view)
    if is_expired(view.get("expires_at_utc"), now):
        out["liveness_assertion"] = HONEST_EMPTY_SESSIONS
        out["running"] = []
        out["scalars_actual"] = _honest_empty_scalars(view.get("scalars_actual") or {})
        out["registry_status_counts"] = {}
        return out

    running: list[dict[str, Any]] = []
    for row in view.get("running") or []:
        if not isinstance(row, dict):
            continue
        if is_expired(row.get("expires_at_utc"), now):
            continue
        running.append(row)
    out["running"] = running
    out["scalars_actual"] = _recompute_scalars(view, running)
    return out


def render_text(view: dict[str, Any], *, now: float | None = None) -> str:
    """Format a served view as a codeblind operator-readable text report."""
    view = serve_view(view, now=now)
    lines: list[str] = [
        "=== what-is-running v1 ===",
        f"observed_at: {view['observed_at_utc']}",
        f"expires_at: {view.get('expires_at_utc', '?')}",
        f"sources: {json.dumps(view['sources'], sort_keys=True)}",
        "",
        "## Ontology nouns (do not collapse)",
    ]
    for k, v in view["ontology"].items():
        lines.append(f"  {k}: {v}")
    s = view["scalars_actual"]
    lines += [
        "",
        "## Scalars (actual — labeled)",
        f"  streams (running_count):     {s['streams_running_count']}",
        f"  attachments (live_cse_count): {s['attachments_live_cse_count']}",
        f"  registry hosts (capacity):   {s['registry_capacity_count']}",
        f"  effective_count (drain ONLY, ≠ admission): "
        f"{s['effective_count_drain_only']}",
        f"  at_soft_limit={s['at_soft_limit']} at_hard_limit={s['at_hard_limit']} "
        f"free_slots={s['free_slots']}",
        f"  registry_status_counts: {view['registry_status_counts']}",
        "",
        "## Running streams",
    ]
    if view.get("liveness_assertion") == HONEST_EMPTY_SESSIONS:
        lines.append(f"  {HONEST_EMPTY_SESSIONS}")
    elif not view["running"]:
        lines.append("  (none)")
    for r in view["running"]:
        age = r.get("age_s")
        age_s = f"{age:.0f}s" if isinstance(age, float) else "?"
        lines.append(
            "  - "
            f"execution_id={r.get('execution_id')} kind={r.get('kind')} "
            f"purpose={r.get('purpose')} lane={r.get('lane')} "
            f"session={r.get('session_url')} reg={r.get('registration_id')} "
            f"reg_status={r.get('registration_status')} age={age_s} "
            f"commissioned_by={r.get('commissioned_by')}"
        )
    lines += ["", "## Intended vs actual"]
    lines.append(f"  intended: {json.dumps(view['intended'], sort_keys=True)}")
    for f in view["findings"]:
        lines.append(f"  [{f.get('verdict')}] {f.get('detail')}")
    life = view["life_reachability"]
    lines += [
        "",
        "## Life reachability",
        f"  live_probe_verbs_on_life: {life['live_probe_verbs_on_life']}",
        f"  life_path: {life['life_path']}",
        f"  code_path: {life['code_path']}",
        "",
    ]
    return "\n".join(lines)
