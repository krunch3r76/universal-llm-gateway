"""G3 auto-enqueue + F1 repair mint + idempotent reconciliation sweep.

Harvest and independent callers mint follow-on todos from actionable charter
frictions. Deterministic slugs make re-runs safe.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from universal_logging import get_logger

from ..db import cortex_conn, query
from ._friction_detent import classify_friction_detent
from ._recon_seed import seed_recon_todo
from .state_card import merge_state_card

logger = get_logger("cortex-api.dispatch_ops.friction_enqueue")

_DISPATCH_LANE = "path-sim-admit-gate"
_SLUG_SAFE_RE = re.compile(r"[^a-z0-9]+")


def normalize_charter_root(root: str) -> str:
    """Bare bus thread id digits — strip optional ``agent-bus:`` prefix."""
    text = str(root or "").strip()
    if text.lower().startswith("agent-bus:"):
        return text.split(":", 1)[1].strip()
    return text


def friction_todo_slug(assertion_id: int, note: str) -> str:
    """Deterministic G3 slug: ``todo:friction-<id>-<short-slug>``."""
    prefix = (note or "friction").strip().lower()[:40]
    slug_part = _SLUG_SAFE_RE.sub("-", prefix).strip("-") or "item"
    return f"todo:friction-{assertion_id}-{slug_part}"


def repair_todo_slug(root_id: str, window_index: int) -> str:
    """F1 deterministic repair slug per (root, window_index)."""
    root = normalize_charter_root(root_id)
    return f"todo:frictions-audit-{root}-w{window_index}"


def _parse_attributes(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        import json

        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


_TODO_CLOSED_WORKFLOW_STATES = frozenset({"done", "deferred", "cancelled", "blocked"})


def todo_exists_for_friction(assertion_id: int) -> str | None:
    """Return the todo slug when one cites ``spawned_by_friction=<id>``."""
    with cortex_conn() as conn:
        rows = query(
            conn,
            "SELECT id FROM entities WHERE type = 'todo' "
            "AND CAST(json_extract(attributes, '$.spawned_by_friction') AS INTEGER) = ? "
            "LIMIT 1",
            (assertion_id,),
        )
    if not rows:
        return None
    return str(rows[0]["id"])


def todo_open_for_friction(assertion_id: int) -> str | None:
    """Return follow-on slug only when workflow_state is not in the closed set."""
    with cortex_conn() as conn:
        rows = query(
            conn,
            "SELECT id, workflow_state FROM entities WHERE type = 'todo' "
            "AND CAST(json_extract(attributes, '$.spawned_by_friction') AS INTEGER) = ? "
            "LIMIT 1",
            (assertion_id,),
        )
    if not rows:
        return None
    workflow_state = rows[0].get("workflow_state")
    if workflow_state is not None and str(workflow_state) in _TODO_CLOSED_WORKFLOW_STATES:
        return None
    return str(rows[0]["id"])


def todo_resolved_for_friction(assertion_id: int) -> bool:
    """True when a follow-on todo exists in a closed workflow_state set."""
    with cortex_conn() as conn:
        rows = query(
            conn,
            "SELECT workflow_state FROM entities WHERE type = 'todo' "
            "AND CAST(json_extract(attributes, '$.spawned_by_friction') AS INTEGER) = ? "
            "LIMIT 1",
            (assertion_id,),
        )
    if not rows:
        return False
    workflow_state = rows[0].get("workflow_state")
    if workflow_state is None:
        return False
    return str(workflow_state) in _TODO_CLOSED_WORKFLOW_STATES


def repair_todo_exists(root_id: str, window_index: int) -> bool:
    """Dedup F1 repair todos by deterministic slug."""
    from .ops_entities import _op_entity_get

    existing = _op_entity_get(entity_id=repair_todo_slug(root_id, window_index))
    return isinstance(existing, dict) and "error" not in existing


def _friction_actionable(attrs: dict[str, Any]) -> bool:
    if attrs.get("defer_enqueue"):
        return False
    actionable = attrs.get("actionable")
    if actionable is False:
        return False
    return True


def mint_friction_followon(
    friction_row: dict[str, Any],
    *,
    root_id: str,
    agent: str = "charter-runner",
    session_id: str = "friction-enqueue",
) -> str | None:
    """Mint G3 follow-on todo for one actionable friction row; return todo slug."""
    assertion_id = int(friction_row["id"])
    existing = todo_exists_for_friction(assertion_id)
    if existing:
        return existing

    attrs = _parse_attributes(friction_row.get("attributes"))
    if not _friction_actionable(attrs):
        return None

    claim = str(friction_row.get("claim") or "")
    note_match = re.match(r"^\[[^\]]+\]\s*(.+)", claim)
    note = (note_match.group(1) if note_match else claim).strip()
    # Stable slug by assertion id only — note text must not fork parallel todos
    # for the same friction (dogfood a:26235 dual-slug orphan).
    todo_id = f"todo:friction-{assertion_id}"
    root = normalize_charter_root(root_id or str(attrs.get("charter_root") or ""))
    suggestion = str(attrs.get("suggestion") or friction_row.get("suggestion") or "")
    category = ""
    cat_m = re.match(r"^\[([^\]]+)\]", claim)
    if cat_m:
        category = cat_m.group(1).strip()
    detent = classify_friction_detent(
        claim=claim,
        note=note,
        suggestion=suggestion,
        category=category,
    )

    extra: dict[str, Any] = merge_state_card(
        {
            "dispatch_lane": _DISPATCH_LANE,
            "followon_of": root,
            "spawned_by_friction": assertion_id,
            "detent": detent,
        }
    )
    if detent == "closed":
        # Closed recipe skips R-after unless escalated mid-window.
        extra.setdefault("check_requested", False)

    result = seed_recon_todo(
        todo_id=todo_id,
        name=f"[followon root {root}] {note[:100]}",
        source_uri=f"cortex://notes/system/specs/friction-{assertion_id}.md",
        required_skills=[],
        seed_ack=f"auto-enqueue from friction #{assertion_id} / root {root}",
        context_target_id=str(friction_row.get("entity_id") or "service:charter-runner"),
        extra_attrs=extra,
        agent=agent,
        session_id=session_id,
    )
    if result and "todo_created" in result:
        from ._shared import record

        record(
            "cortex.friction.todo.enqueued",
            assertion_id=assertion_id,
            todo_id=todo_id,
            charter_root=root,
        )
        return todo_id
    if result and "error" not in result:
        return todo_id
    return None


def mint_repair_todo(
    *,
    root_id: str,
    window_index: int,
    audit_failure_class: str,
    agent: str = "charter-runner",
    session_id: str = "friction-audit-repair",
) -> dict[str, Any] | None:
    """F1 — exactly one repair todo per failed (root, window_index)."""
    todo_id = repair_todo_slug(root_id, window_index)
    root = normalize_charter_root(root_id)
    if repair_todo_exists(root_id, window_index):
        return None

    result = seed_recon_todo(
        todo_id=todo_id,
        name=f"[followon root {root}] Frictions audit repair w{window_index}",
        source_uri="cortex://notes/system/specs/friction-charter-feedback-loop.md",
        required_skills=[],
        seed_ack=(
            f"G1 frictions audit failed ({audit_failure_class}) "
            f"root={root} window={window_index}"
        ),
        context_target_id=f"agent-bus:{root}",
        extra_attrs=merge_state_card(
            {
                "dispatch_lane": _DISPATCH_LANE,
                "followon_of": root,
                "audit_failure_class": audit_failure_class,
            }
        ),
        agent=agent,
        session_id=session_id,
    )
    if result and "todo_created" in result:
        from ._shared import record

        record(
            "cortex.friction.todo.repair_enqueued",
            charter_root=root,
            window_index=window_index,
            audit_failure_class=audit_failure_class,
            todo_id=todo_id,
        )
    return result


def reconcile_charter_frictions(
    root_id: str,
    *,
    frictions_fn: Callable[..., dict[str, Any]] | None = None,
    agent: str = "charter-runner",
) -> list[dict[str, Any]]:
    """Idempotent sweep — mint todos for actionable frictions lacking one."""
    from .ops_assertions import _op_frictions

    list_fn = frictions_fn or _op_frictions
    root = normalize_charter_root(root_id)
    resp = list_fn(
        charter_root=root,
        actionable=True,
        superseded=False,
        limit=200,
        intent="full",
    )
    if resp.get("error"):
        logger.warning("reconcile frictions query failed root=%s: %s", root, resp["error"])
        return []

    minted: list[dict[str, Any]] = []
    for row in resp.get("items") or []:
        if not isinstance(row, dict):
            continue
        try:
            assertion_id = int(row["id"])
        except (KeyError, TypeError, ValueError):
            continue
        if todo_exists_for_friction(assertion_id):
            continue
        created = mint_friction_followon(row, root_id=root, agent=agent)
        if created:
            minted.append({"todo_id": created, "assertion_id": assertion_id})
    return minted


def file_charter_protocol_friction(
    *,
    root_id: str,
    window_index: int,
    note: str,
    category: str = "protocol",
    scoreboard_uri: str | None = None,
    agent: str = "charter-runner",
    actionable: bool = False,
    actionable_false_reason: str | None = None,
) -> int | None:
    """File a machine protocol friction; return assertion id or None."""
    from .ops_assertions_write import _op_friction

    root = normalize_charter_root(root_id)
    kwargs: dict[str, Any] = {
        "owner": "service:charter-runner",
        "category": category,
        "note": note,
        "agent": agent,
        "charter_root": root,
        "window_index": window_index,
        "actionable": actionable,
    }
    if scoreboard_uri:
        kwargs["scoreboard_uri"] = scoreboard_uri
    if actionable_false_reason:
        kwargs["actionable_false_reason"] = actionable_false_reason
    elif not actionable:
        kwargs["actionable_false_reason"] = "machine-recovery informational"

    try:
        result = _op_friction(**kwargs)
    except Exception as exc:  # noqa: BLE001 — recovery must not abort on cortex miss
        logger.warning("file_charter_protocol_friction raised: %s", exc)
        return None
    if "error" in result:
        logger.warning("file_charter_protocol_friction failed: %s", result["error"])
        return None
    item = result.get("item") or {}
    try:
        return int(item.get("id"))
    except (TypeError, ValueError):
        return None


def frictions_checkpoint_line(
    friction_id: int, *, category: str = "protocol", note: str
) -> str:
    """Canonical CHECKPOINT ``## Frictions`` bullet."""
    return f"- [filed assertion:{friction_id}] {category}: {note}"


__all__ = [
    "file_charter_protocol_friction",
    "friction_todo_slug",
    "frictions_checkpoint_line",
    "mint_friction_followon",
    "mint_repair_todo",
    "normalize_charter_root",
    "reconcile_charter_frictions",
    "repair_todo_exists",
    "repair_todo_slug",
    "todo_exists_for_friction",
    "todo_open_for_friction",
    "todo_resolved_for_friction",
]
