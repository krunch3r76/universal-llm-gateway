"""Life stop evaluation and policy knobs (§ Stops (b), F7).

Pure rules from life-orchestrator-navigator phase-2 bind § Stops (b): seven stop
classes in table order, knob defaults for liaison-tick --set passthrough.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from bus_watch.consent_projection import is_gated

LIFE_KNOB_DEFAULTS: dict[str, Any] = {
    "wake_cron": "0 */4 * * *",
    "spend_cap_dispatches_per_day": 12,
    "context_budget_ratio": 0.80,
    "repeated_failure_n": 2,
    "waiting_idle_h": 72,
    "deadline_near_days": 3,
    "let_drive_ttl_days": 30,
    "digest_max_age_s": 900,
    "check_model": "cdp/opus-5",
}


def _as_dt(value: datetime | str) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    text = str(value).replace("Z", "+00:00")
    dt = datetime.fromisoformat(text)
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _iso(value: datetime | str) -> str:
    return (
        _as_dt(value)
        .astimezone(UTC)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def _knob_value(knobs: dict[str, dict[str, Any]], key: str) -> Any:
    return knobs[key]["value"]


def life_knobs(
    policy: dict[str, Any],
    *,
    as_of: datetime | str,
) -> dict[str, dict[str, Any]]:
    """Resolve life knobs from policy overrides or LIFE_KNOB_DEFAULTS."""
    as_of_iso = _iso(as_of)
    out: dict[str, dict[str, Any]] = {}
    for key, default in LIFE_KNOB_DEFAULTS.items():
        if key in policy:
            out[key] = {"value": policy[key], "source": "policy", "as_of": as_of_iso}
        else:
            out[key] = {"value": default, "source": "default", "as_of": as_of_iso}
    return out


def evaluate_stops(
    facts: dict[str, Any],
    knobs: dict[str, dict[str, Any]],
    *,
    now: datetime | str,
) -> list[dict[str, Any]]:
    """Return firing stop rows in spec table order."""
    now_dt = _as_dt(now)
    now_iso = _iso(now_dt)
    rows: list[dict[str, Any]] = []

    next_move = facts.get("next_move") or {}
    gates_block = facts.get("gates") or {}
    move_class = str(next_move.get("class") or "")
    move_scope = next_move.get("scope")
    goal_id = next_move.get("goal_id")

    if next_move and is_gated(move_class, move_scope, gates_block):
        rows.append(
            {
                "stop": "OPERATOR_GATE",
                "row": goal_id,
                "trigger": f"move {move_class} not lifted for scope {move_scope!r}",
                "action": "park_row",
                "page": True,
                "carries": {
                    "paragraph": next_move.get("draft") or "",
                    "draft": next_move.get("draft") or "",
                    "answer": "send / hold",
                },
                "as_of": now_iso,
            }
        )

    if facts.get("check_disagrees") or facts.get("person_fact_missing"):
        question = (
            "check disagrees" if facts.get("check_disagrees") else "person fact missing"
        )
        rows.append(
            {
                "stop": "CONSULT_PENDING",
                "row": goal_id,
                "trigger": question,
                "action": "pin_row",
                "page": False,
                "carries": {
                    "to": _knob_value(knobs, "check_model"),
                    "question": question,
                    "answer": "one",
                },
                "as_of": now_iso,
            }
        )

    cap = int(_knob_value(knobs, "spend_cap_dispatches_per_day"))
    dispatches = int(facts.get("dispatches_today") or 0)
    if dispatches >= cap:
        rows.append(
            {
                "stop": "SPEND_CAP",
                "row": goal_id,
                "trigger": f"dispatches_today={dispatches} >= cap={cap}",
                "action": "pause_dispatches",
                "page": True,
                "carries": {
                    "count": dispatches,
                    "cap": cap,
                    "waiting_row": goal_id,
                },
                "as_of": now_iso,
            }
        )

    ratio_threshold = float(_knob_value(knobs, "context_budget_ratio"))
    context_ratio = float(facts.get("context_ratio") or 0)
    if context_ratio >= ratio_threshold:
        rows.append(
            {
                "stop": "CONTEXT_BUDGET",
                "row": None,
                "trigger": f"context_ratio={context_ratio} >= {ratio_threshold}",
                "action": "fold_end_turn",
                "page": False,
                "carries": {},
                "as_of": now_iso,
            }
        )

    fail_n = int(_knob_value(knobs, "repeated_failure_n"))
    move_failures = facts.get("move_failures") or {}
    for move_key, info in move_failures.items():
        if info.get("standing"):
            continue
        n = int(info.get("n") or 0)
        if n >= fail_n:
            rows.append(
                {
                    "stop": "REPEATED_FAILURE",
                    "row": info.get("goal_id"),
                    "trigger": f"move {move_key!r} failed {n}x >= {fail_n}",
                    "action": "stop_row",
                    "page": True,
                    "carries": {
                        "move": move_key,
                        "n": n,
                        "friction": True,
                        "alternative": "one",
                    },
                    "as_of": now_iso,
                }
            )
            break

    idle_h = float(_knob_value(knobs, "waiting_idle_h"))
    last_progress = facts.get("last_progress") or {}
    goals = facts.get("goals") or []
    for goal in goals:
        gid = str(goal.get("id") or "")
        if gid not in last_progress:
            continue
        last_ts = _as_dt(last_progress[gid])
        idle = now_dt - last_ts
        if idle >= timedelta(hours=idle_h):
            rows.append(
                {
                    "stop": "WAITING_ON_WORLD",
                    "row": gid,
                    "trigger": f"no progress for {idle_h}h on {gid}",
                    "action": "park_silent",
                    "page": False,
                    "carries": {"nudge_draft_to_gate": True},
                    "as_of": now_iso,
                }
            )
            break

    near_days = int(_knob_value(knobs, "deadline_near_days"))
    for goal in goals:
        deadline_raw = goal.get("deadline")
        if not deadline_raw:
            continue
        deadline_dt = _as_dt(deadline_raw)
        remaining = deadline_dt - now_dt
        if remaining <= timedelta(days=near_days):
            gid = str(goal.get("id") or "")
            nm = next_move if next_move.get("goal_id") == gid else {}
            nm_class = str(nm.get("class") or "")
            nm_scope = nm.get("scope")
            page = is_gated(nm_class, nm_scope, gates_block) if nm else False
            rows.append(
                {
                    "stop": "DEADLINE_NEAR",
                    "row": gid,
                    "trigger": f"deadline within {near_days}d for {gid}",
                    "action": "promote_to_now",
                    "page": page,
                    "carries": {
                        "deadline": str(deadline_raw),
                        "draft": nm.get("draft") or "",
                        "answer": "one",
                    },
                    "as_of": now_iso,
                }
            )
            break

    return rows
