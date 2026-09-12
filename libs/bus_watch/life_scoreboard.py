"""Life scoreboard NOW projection and spawn template (F4/F5).

Pure rules from life-orchestrator-navigator phase-2 bind § F4/F5: first open goal
by deadline/priority excluding standing blocking obstacles; sparse spawn markdown.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

NOW_RULE = "first-open-goal-by-deadline-priority-nonstanding"


def _as_dt(value: datetime | str | date) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, date) and not isinstance(value, datetime):
        return datetime(value.year, value.month, value.day, tzinfo=UTC)
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


def _deadline_key(deadline: str | None) -> tuple[int, str]:
    if deadline is None:
        return (1, "")
    return (0, deadline)


def _priority_key(priority: int | None) -> tuple[int, int]:
    if priority is None:
        return (1, 0)
    return (0, priority)


def _has_standing_block(obstacles: list[dict[str, Any]]) -> bool:
    for obs in obstacles:
        if obs.get("blocking") and obs.get("standing"):
            return True
    return False


def _standing_obstacle_id(obstacles: list[dict[str, Any]]) -> str | None:
    for obs in obstacles:
        if obs.get("blocking") and obs.get("standing"):
            return str(obs.get("id") or "")
    return None


def project_now(
    goals: list[dict[str, Any]],
    *,
    as_of: datetime | str,
) -> dict[str, Any] | None:
    """Select NOW goal: open, non-standing-blocked, deadline then priority."""
    as_of_iso = _iso(as_of)
    skipped: list[dict[str, str]] = []
    candidates: list[dict[str, Any]] = []

    for goal in goals:
        gid = str(goal.get("id") or "")
        status = str(goal.get("status") or "")
        obstacles = list(goal.get("obstacles") or [])

        if status != "open":
            skipped.append({"goal_id": gid, "reason": f"status:{status}"})
            continue
        standing_id = _standing_obstacle_id(obstacles)
        if standing_id:
            skipped.append(
                {"goal_id": gid, "reason": f"standing_obstacle:{standing_id}"}
            )
            continue
        candidates.append(goal)

    if not candidates:
        return None

    candidates.sort(
        key=lambda g: (
            _deadline_key(g.get("deadline")),
            _priority_key(g.get("priority")),
            str(g.get("id") or ""),
        )
    )
    winner = candidates[0]
    win_id = str(winner.get("id") or "")
    source = [win_id]
    for obs in winner.get("obstacles") or []:
        oid = str(obs.get("id") or "")
        if oid:
            source.append(oid)

    return {
        "goal_id": win_id,
        "rule": NOW_RULE,
        "source": source,
        "as_of": as_of_iso,
        "skipped": skipped,
    }


def _render_obstacles(obstacles: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for obs in obstacles:
        oid = str(obs.get("id") or "")
        if obs.get("standing"):
            parts.append(f"{oid} (standing)")
        else:
            parts.append(oid)
    return ", ".join(parts) if parts else "—"


def render_spawn_scoreboard(
    slug: str,
    matter_id: str,
    goals: list[dict[str, Any]],
    *,
    as_of: datetime | str,
    spec_sha: str = "9b2ca21a",
) -> str:
    """Sparse-at-spawn markdown scoreboard for a life endeavor."""
    as_of_iso = _iso(as_of)
    now_row = project_now(goals, as_of=as_of)
    lines = [
        f"# life-{slug} — scoreboard (endeavor {matter_id})",
        "",
        (
            f"Purpose: life navigator scoreboard for plan:life-orchestrator-navigator; "
            f"phase-2 bind sha {spec_sha}."
        ),
        "",
        "## NOW",
        "| goal | rule | source | as_of |",
        "|---|---|---|---|",
    ]
    if now_row:
        source = ", ".join(now_row["source"])
        lines.append(
            f"| {now_row['goal_id']} | {now_row['rule']} | {source} | {as_of_iso} |"
        )
    else:
        lines.append(f"| _none_ | {NOW_RULE} | — | {as_of_iso} |")

    lines.extend(
        [
            "",
            "## Goals",
            "| goal | status | deadline | obstacles (standing) | next move | cites |",
            "|---|---|---|---|---|---|",
        ]
    )
    for goal in goals:
        gid = str(goal.get("id") or "")
        status = str(goal.get("status") or "")
        deadline = goal.get("deadline") if goal.get("deadline") is not None else "—"
        obstacles = _render_obstacles(list(goal.get("obstacles") or []))
        lines.append(
            f"| {gid} | {status} | {deadline} | {obstacles} | _none_ | {gid} |"
        )

    lines.extend(
        [
            "",
            "## Steer log",
            (
                "| ts | class | utterance_sha | scope | expiry | assertion_id | "
                "NOW before -> after | wake chat |"
            ),
            "| _none_ | | | | | | | |",
            "",
            "## Stops log",
            "| ts | stop | row | carries | resolved |",
            "| _none_ | | | | |",
            "",
        ]
    )
    return "\n".join(lines)
