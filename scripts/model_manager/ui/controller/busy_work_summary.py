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
