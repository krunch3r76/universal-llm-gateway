"""TODO retrieval dispatch ops."""

from __future__ import annotations

from typing import Any

from ..db import cortex_conn
from ..routes.todo_audit import get_todo_audit
from ..routes.todo_retrieval import _query_todo_candidates


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
