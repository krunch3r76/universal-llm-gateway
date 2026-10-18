"""Friction read ops — list open friction assertions with charter and continuity anchor filters.

Separated from `ops_assertions_friction` so write and read paths stay within the SLOC ceiling.
Callers import `_op_frictions` from here or via the `ops_assertions` umbrella module.
"""

from __future__ import annotations

from typing import Any

from ..routes.assertions import _list_assertions_impl
from ._friction_charter_attrs import (
    _friction_charter_filters,
    _project_friction_full_items,
    _project_friction_summary_items,
)
from ._shared import _FRICTION_OWNER_TYPES, owner_entity_id
from .ops_assertions_friction import _resolve_friction_owner_entity_id
from .workflow_hints import _WORKFLOW_HINTS


def _anchor_filters_active(
    *,
    charter_root: str | None,
    window_index: int | None,
    actionable: bool | None,
    since: str | None,
    anchor_kind: str | None,
    anchor_root: str | None,
    anchor_seq: int | None,
) -> bool:
    return any(
        v is not None
        for v in (
            charter_root,
            window_index,
            actionable,
            since,
            anchor_kind,
            anchor_root,
            anchor_seq,
        )
    )


def _op_frictions(
    owner: str | None = None,
    owner_type: str | None = None,
    service: str | None = None,
    category: str | None = None,
    seeded_by: str | None = None,
    charter_root: str | None = None,
    window_index: int | None = None,
    anchor_kind: str | None = None,
    anchor_root: str | None = None,
    anchor_seq: int | None = None,
    actionable: bool | None = None,
    since: str | None = None,
    superseded: bool | None = None,
    limit: int | None = None,
    intent: str | None = None,
    include_compaction_pointers: bool = False,
    **_: object,
) -> dict[str, Any]:
    """List open friction assertions across friction-owning entities
    (service:/agent_skill:/ai_agent:), bracketed [category] claims."""
    resolved_intent = intent if intent in ("summary", "full") else "summary"
    if owner_type is not None and owner_type not in _FRICTION_OWNER_TYPES:
        return {
            "error": f"Invalid owner_type {owner_type!r}. Must be one of: {list(_FRICTION_OWNER_TYPES)}"
        }
    owner_arg = owner if owner is not None else service
    entity_id = owner_entity_id(owner_arg) if owner_arg else None
    # Same resolve as _op_friction so list(owner=service:agent_bus) finds rows
    # written after underscore→hyphen alias (a:37201 review A3). On miss keep
    # the raw id (empty list) rather than erroring the list path.
    if entity_id is not None:
        resolved = _resolve_friction_owner_entity_id(entity_id)
        if not isinstance(resolved, dict):
            entity_id = resolved
    entity_type = owner_type if (owner_type and entity_id is None) else None
    entity_type_in = (
        list(_FRICTION_OWNER_TYPES) if (entity_id is None and owner_type is None) else None
    )
    claim_filter = f"[{category}]" if category else None
    filters_active = _anchor_filters_active(
        charter_root=charter_root,
        window_index=window_index,
        actionable=actionable,
        since=since,
        anchor_kind=anchor_kind,
        anchor_root=anchor_root,
        anchor_seq=anchor_seq,
    )
    fetch_intent = "full" if filters_active else resolved_intent
    fetch_limit = limit or (50 if fetch_intent == "full" else 7)
    from ..routes.assertions import _list_assertions_impl as _orig_list_impl
    from . import ops_assertions as _ops_assertions_mod
    from . import ops_assertions_friction as _ops_friction_mod

    list_fn = _list_assertions_impl
    if list_fn is _orig_list_impl:
        for candidate in (
            getattr(_ops_assertions_mod, "_list_assertions_impl", _orig_list_impl),
            getattr(_ops_friction_mod, "_list_assertions_impl", _orig_list_impl),
        ):
            if candidate is not _orig_list_impl:
                list_fn = candidate
                break
    result = list_fn(
        entity_id=entity_id,
        entity_id_prefix=None,
        entity_type=entity_type,
        entity_type_in=entity_type_in,
        claim_filter=claim_filter,
        seeded_by=seeded_by,
        superseded=False if superseded is None else superseded,
        limit=fetch_limit,
        intent=fetch_intent,
        include_compaction_pointers=include_compaction_pointers,
    )
    if result.get("error"):
        return result

    raw_items: list[dict[str, Any]] = []
    for item in result.get("items") or []:
        if isinstance(item, dict):
            raw_items.append(item)
        elif hasattr(item, "model_dump"):
            raw_items.append(item.model_dump(mode="json"))

    if filters_active:
        filtered = _friction_charter_filters(
            raw_items,
            charter_root=charter_root,
            window_index=window_index,
            actionable=actionable,
            since=since,
            anchor_kind=anchor_kind,
            anchor_root=anchor_root,
            anchor_seq=anchor_seq,
        )
        if resolved_intent == "summary":
            result["items"] = _project_friction_summary_items(filtered[: (limit or 7)])
        else:
            result["items"] = _project_friction_full_items(filtered[: (limit or 50)])
        result["intent"] = resolved_intent
    elif resolved_intent == "summary":
        pass
    elif resolved_intent == "full":
        result["items"] = _project_friction_full_items(raw_items[: (limit or 50)])
    if not result.get("error"):
        # Handler-set _next shadows _WORKFLOW_HINTS["frictions"] (dispatch
        # __init__). Emit the static hint (ticket cycle + friction_close), not a
        # second inline copy that vocabulary sweeps can miss (a:37439).
        frictions_next = _WORKFLOW_HINTS["frictions"]
        if resolved_intent == "summary":
            result["_next"] = (
                "Deepen one row: cortex(tool=assertion_get, assertion_id=<id>). "
                "Full rows: re-call with intent=full. " + frictions_next
            )
        else:
            result["_next"] = frictions_next
    return result


__all__ = ["_op_frictions"]
