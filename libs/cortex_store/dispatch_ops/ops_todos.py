"""TODO retrieval + closure-sidecar dispatch ops."""

from __future__ import annotations

from typing import Any

from ..db import cortex_conn
from ..routes.todo_audit import get_todo_audit
from ..routes.todo_retrieval import _query_todo_candidates
from ._todo_closure_sidecar import (
    closure_sidecar_uri,
    render_closure_markdown,
    slug_from_todo_id,
    write_closure_sidecar,
)
from .ops_entities import _op_entity_update


def _op_todo_candidates(
    q: str | None = None,
    query: str | None = None,
    limit: int | None = None,
    workflow_state: str | None = "open",
    priority: str | None = None,
    domain: str | None = None,
    domain_exclude: str | None = None,
    context: str | None = None,
    **_: object,
) -> dict[str, Any]:
    with cortex_conn() as conn:
        return _query_todo_candidates(
            conn,
            q=q or query,
            limit=limit or 10,
            workflow_state=workflow_state,
            priority=priority,
            domain=domain,
            domain_exclude=domain_exclude,
            context=context,
        )


def _op_todo_audit(
    stale_days: int | None = None,
    limit: int | None = None,
    domain: str | None = None,
    priority: str | None = None,
    **_: object,
) -> dict[str, Any]:
    return get_todo_audit(
        stale_days=stale_days or 60,
        limit=limit or 50,
        domain=domain,
        priority=priority,
    )


def _op_todo_close_sidecar(
    todo_id: str | None = None,
    summary: str | None = None,
    evidence: str | None = None,
    reasoning_summary: str | None = None,
    references: list[dict[str, Any]] | None = None,
    agent: str | None = None,
    session_id: str | None = None,
    closed_at: str | None = None,
    **_: object,
) -> dict[str, Any]:
    """Write the standardized closure markdown sidecar + set the entity pointer.

    Produces ``notes/system/todos/{slug}-closure.md`` under the cortex sandbox
    and sets ``attributes.closure_summary_uri`` on the todo entity (merge —
    existing attributes are preserved). Returns the canonical URI so the caller
    can cite it in the closure assertion's ``evidence_uris``.
    """
    if not todo_id or not summary:
        return {"error": "todo_id and summary are required"}

    slug = slug_from_todo_id(todo_id)
    uri = closure_sidecar_uri(slug)
    content = render_closure_markdown(
        todo_id=todo_id,
        summary=summary,
        evidence=evidence,
        reasoning_summary=reasoning_summary,
        references=references,
        agent=agent,
        session_id=session_id,
        closed_at=closed_at,
    )
    try:
        path = write_closure_sidecar(slug, content)
    except OSError as exc:
        return {"error": f"sidecar_write_failed: {exc}"}

    update = _op_entity_update(
        entity_id=todo_id, attributes={"closure_summary_uri": uri}
    )
    result: dict[str, Any] = {
        "ok": "error" not in update,
        "todo_id": todo_id,
        "closure_summary_uri": uri,
        "path": path,
    }
    if "error" in update:
        result["attribute_update_error"] = update["error"]
    return result
