"""One-line human summary of an active-work probe payload.

Used by TUI deferral logs and ``manage.restart.deferred`` so operators see
*what* is holding a restart — not only ``busy: true``. Prefer ``write_lease``
holder fields when present; fall back to ``active_ops`` / count keys.
"""

from __future__ import annotations

from typing import Any


def format_active_work_summary(active_work: dict[str, Any] | None) -> str:
    """Return a compact holder line, or empty string when nothing useful is known."""
    if not isinstance(active_work, dict) or not active_work:
        return ""
    if active_work.get("error"):
        return f"probe_error={active_work['error']}"

    count = _active_count(active_work)
    holder = _holder_clause(active_work)
    ops = _ops_clause(active_work)
    parts: list[str] = []
    if count is not None:
        parts.append(f"active_count={count}")
    if holder:
        parts.append(holder)
    elif ops:
        parts.append(ops)
    orphan = _lane_b_orphan_clause(active_work)
    if orphan:
        parts.append(orphan)
    hygiene = _lane_hygiene_clause(active_work)
    if hygiene:
        parts.append(hygiene)
    return "; ".join(parts)


def _active_count(work: dict[str, Any]) -> int | None:
    for key in ("active_count", "total", "running_count", "running"):
        val = work.get(key)
        if isinstance(val, int):
            return val
    return None


def _holder_clause(work: dict[str, Any]) -> str:
    lease = work.get("write_lease")
    if not isinstance(lease, dict):
        return ""
    dispatch_id = lease.get("holder_dispatch_id")
    if not dispatch_id:
        return ""
    bits = [f"holder={_short_id(str(dispatch_id))}"]
    model = lease.get("holder_resolved_model")
    if isinstance(model, str) and model.strip():
        bits.append(f"model={model.strip()}")
    subject = lease.get("holder_subject_preview")
    if isinstance(subject, str) and subject.strip():
        bits.append(f'subject="{_clip(subject.strip(), 80)}"')
    status = lease.get("holder_status")
    if isinstance(status, str) and status.strip():
        bits.append(f"status={status.strip()}")
    return " ".join(bits)


def _lane_b_orphan_clause(work: dict[str, Any]) -> str:
    lane_b = work.get("lane_b")
    if not isinstance(lane_b, dict):
        stats = work.get("concurrency_stats")
        if isinstance(stats, dict):
            aged = stats.get("lane_b_aged_orphans")
            if isinstance(aged, list) and aged:
                return _format_aged_orphan_line(aged[0])
        return ""
    aged = lane_b.get("aged_orphans")
    if not isinstance(aged, list) or not aged:
        return ""
    return _format_aged_orphan_line(aged[0])


def _lane_hygiene_clause(work: dict[str, Any]) -> str:
    """Open branch debt, named beside the lane that owes it."""
    hygiene = None
    lane_b = work.get("lane_b")
    if isinstance(lane_b, dict):
        hygiene = lane_b.get("lane_hygiene")
    if not isinstance(hygiene, dict):
        stats = work.get("concurrency_stats")
        if isinstance(stats, dict):
            hygiene = stats.get("lane_b_hygiene")
    if not isinstance(hygiene, dict):
        return ""
    open_debts = hygiene.get("open_debts")
    if not isinstance(open_debts, int) or open_debts < 1:
        return ""
    bits = [f"branch_debt={open_debts}"]
    by_lane = hygiene.get("debts_by_lane")
    if isinstance(by_lane, dict) and by_lane:
        worst = max(by_lane.items(), key=lambda kv: kv[1])
        bits.append(f"owing_lane={worst[0]}({worst[1]})")
    oldest = hygiene.get("oldest_debt_age_s")
    if isinstance(oldest, (int, float)):
        bits.append(f"oldest_s={int(oldest)}")
    return " ".join(bits)


def _format_aged_orphan_line(entry: dict[str, Any]) -> str:
    branch = entry.get("branch")
    tip = entry.get("tip_sha")
    age_s = entry.get("age_s")
    origin = entry.get("origin_dispatch_id")
    if not isinstance(branch, str):
        return ""
    tip_short = _short_id(str(tip)) if tip else "?"
    origin_short = _short_id(str(origin)) if origin else "?"
    age_part = f"age_s={int(age_s)}" if isinstance(age_s, (int, float)) else "age_s=?"
    return (
        f"lane_b_orphan branch={branch} tip={tip_short} {age_part} "
        f"origin={origin_short}"
    )


def _ops_clause(work: dict[str, Any]) -> str:
    ops = work.get("active_ops")
    if not isinstance(ops, list) or not ops:
        return ""
    rendered: list[str] = []
    for op in ops[:3]:
        if not isinstance(op, dict):
            continue
        kind = str(op.get("kind") or "op")
        op_id = op.get("op_id")
        piece = f"{kind}:{_short_id(str(op_id))}" if op_id else kind
        model = op.get("resolved_model") or op.get("model")
        if isinstance(model, str) and model.strip():
            piece += f"/{model.strip()}"
        subject = op.get("subject_preview")
        if isinstance(subject, str) and subject.strip():
            piece += f' "{_clip(subject.strip(), 40)}"'
        rendered.append(piece)
    more = len(ops) - len(rendered)
    out = "ops=[" + ", ".join(rendered) + "]"
    if more > 0:
        out += f" +{more}"
    return out


def _short_id(value: str) -> str:
    return value if len(value) <= 12 else value[:8]


def _clip(value: str, limit: int) -> str:
    return value if len(value) <= limit else value[: limit - 1] + "…"
