"""Todo close handler — atomic audit-trail closure orchestration.

Reads the closure payload from `pipeline_options` (delivered as
`context.options`), then executes a fixed sequence of cortex-api dispatch
calls:

  1. assert       — closure summary on the todo entity
  2. relationship_create (per `references` item)
  3. edge_create  (per `depends_on_resolved` assertion id, edge_type=depends_on)
  4. edge_create  (per `edges` item, escape hatch for non-depends_on)
  5. entity_update workflow_state=done  (unless `skip_workflow_update`)

Per-call results are collected into a structured response object so the
agent learns exactly what succeeded and what failed in one call. No retries
— partial failures stay visible; the agent (or operator) can re-call with
the missing pieces. Idempotency is provided by cortex-api's existing
dedup_guard (claim_hash) and unique-constraint behaviour on relationships /
edges.

This handler does NOT call any LLM. Its job is composition over cortex-api
primitives, per the architecture rationale recorded in
todo:cortex-todo-closure-payload (assertions 220, 221, 5184).
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any, override

from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput
from transport_utils import DEFAULT_CORTEX_URL, make_async_client

from ._ops import (
    _DEFAULT_EDGE_TYPE,
    _DEPENDS_ON_CONTEXT,
    default_evidence,
    do_assert,
    do_edge,
    do_relationship,
    do_workflow_update,
    extract_id,
    missing_keys,
)

logger = logging.getLogger(__name__)

_REQUEST_TIMEOUT = 15.0


class TodoCloseApplyHandler(BaseHandler):
    """Single-step handler that runs the full closure sequence."""

    step_type = "todo_close_apply_v1"

    @override
    async def execute(self, step: Any, context: Any) -> StepOutput:
        opts = getattr(context, "options", {}) or {}
        todo_id = opts.get("todo_id")
        summary = opts.get("summary")
        agent = opts.get("agent")
        session_id = opts.get("session_id")

        missing = missing_keys(opts, ("todo_id", "summary"))
        if missing:
            err = {
                "ok": False,
                "error": f"missing required pipeline_options: {missing}",
                "expected": [
                    "todo_id",
                    "summary",
                    "[evidence_uris]",
                    "[evidence_text]",
                    "[reasoning_summary]",
                    "[references]",
                    "[depends_on_resolved]",
                    "[edges]",
                    "[skip_workflow_update]",
                    "[agent]",
                    "[session_id]",
                ],
            }
            return StepOutput(raw=json.dumps(err), json=err, error=err["error"])

        evidence = opts.get("evidence_text") or default_evidence(
            todo_id, agent, session_id
        )
        evidence_uris = opts.get("evidence_uris") or None
        reasoning_summary = opts.get("reasoning_summary") or None
        references = opts.get("references") or []
        depends_on_resolved = opts.get("depends_on_resolved") or []
        custom_edges = opts.get("edges") or []
        skip_workflow_update = bool(opts.get("skip_workflow_update", False))

        result: dict[str, Any] = {
            "ok": True,
            "todo_id": todo_id,
            "assertion": None,
            "assertion_id": None,
            "relationships": [],
            "relationship_ids": [],
            "edges": [],
            "edge_ids": [],
            "workflow_update": None,
            "errors": [],
        }

        async with make_async_client(
            DEFAULT_CORTEX_URL, timeout=_REQUEST_TIMEOUT
        ) as client:
            assertion_resp = await do_assert(
                client,
                todo_id=todo_id,
                summary=summary,
                evidence=evidence,
                evidence_uris=evidence_uris,
                reasoning_summary=reasoning_summary,
                agent=agent,
            )
            result["assertion"] = assertion_resp
            if "error" in assertion_resp:
                result["ok"] = False
                result["errors"].append(
                    {"step": "assert", "error": assertion_resp["error"]}
                )
            else:
                aid = extract_id(assertion_resp, "id", "assertion_id")
                if isinstance(aid, int):
                    result["assertion_id"] = aid

            for idx, item in enumerate(references):
                if not isinstance(item, dict):
                    err = f"references[{idx}] must be a dict"
                    result["ok"] = False
                    result["errors"].append(
                        {"step": f"relationship[{idx}]", "error": err}
                    )
                    result["relationships"].append({"error": err})
                    continue
                rel_resp = await do_relationship(
                    client,
                    todo_id=todo_id,
                    item=item,
                    agent=agent,
                    session_id=session_id,
                )
                result["relationships"].append(rel_resp)
                if "error" in rel_resp:
                    result["ok"] = False
                    result["errors"].append(
                        {"step": f"relationship[{idx}]", "error": rel_resp["error"]}
                    )
                else:
                    rid = extract_id(rel_resp, "id", "relationship_id")
                    if isinstance(rid, int):
                        result["relationship_ids"].append(rid)

            edge_agent = agent or "pipeline:todo-close"
            edge_session = (
                session_id
                or f"todo-close-{datetime.now(UTC).strftime('%Y-%m-%d-%H%M')}"
            )

            for idx, dep in enumerate(depends_on_resolved):
                if not isinstance(dep, int):
                    err = f"depends_on_resolved[{idx}] must be an int assertion_id"
                    result["ok"] = False
                    result["errors"].append(
                        {"step": f"depends_on[{idx}]", "error": err}
                    )
                    result["edges"].append({"error": err})
                    continue
                edge_resp = await do_edge(
                    client,
                    from_node=todo_id,
                    to_node=f"assertion:{dep}",
                    edge_type=_DEFAULT_EDGE_TYPE,
                    context_text=_DEPENDS_ON_CONTEXT,
                    strength=None,
                    agent=edge_agent,
                    session_id=edge_session,
                )
                result["edges"].append({"kind": "depends_on", "to": dep, **edge_resp})
                if "error" in edge_resp:
                    result["ok"] = False
                    result["errors"].append(
                        {"step": f"depends_on[{idx}]", "error": edge_resp["error"]}
                    )
                else:
                    eid = extract_id(edge_resp, "id", "edge_id")
                    if isinstance(eid, int):
                        result["edge_ids"].append(eid)

            for idx, edge in enumerate(custom_edges):
                if not isinstance(edge, dict):
                    err = f"edges[{idx}] must be a dict"
                    result["ok"] = False
                    result["errors"].append({"step": f"edge[{idx}]", "error": err})
                    result["edges"].append({"error": err})
                    continue
                to_node = edge.get("to_node")
                edge_type = edge.get("edge_type")
                if not to_node or not edge_type:
                    err = f"edges[{idx}] requires 'to_node' and 'edge_type'"
                    result["ok"] = False
                    result["errors"].append({"step": f"edge[{idx}]", "error": err})
                    result["edges"].append({"error": err})
                    continue
                edge_resp = await do_edge(
                    client,
                    from_node=todo_id,
                    to_node=to_node,
                    edge_type=edge_type,
                    context_text=edge.get("context"),
                    strength=edge.get("strength"),
                    agent=edge_agent,
                    session_id=edge_session,
                )
                result["edges"].append({"kind": edge_type, "to": to_node, **edge_resp})
                if "error" in edge_resp:
                    result["ok"] = False
                    result["errors"].append(
                        {"step": f"edge[{idx}]", "error": edge_resp["error"]}
                    )
                else:
                    eid = extract_id(edge_resp, "id", "edge_id")
                    if isinstance(eid, int):
                        result["edge_ids"].append(eid)

            if not skip_workflow_update:
                wf_resp = await do_workflow_update(client, todo_id)
                result["workflow_update"] = wf_resp
                if "error" in wf_resp:
                    result["ok"] = False
                    result["errors"].append(
                        {"step": "workflow_update", "error": wf_resp["error"]}
                    )

        logger.info(
            "todo_close: todo=%s assertion_id=%s relationships=%d edges=%d errors=%d",
            todo_id,
            result["assertion_id"],
            len(result["relationship_ids"]),
            len(result["edge_ids"]),
            len(result["errors"]),
        )
        return StepOutput(
            raw=json.dumps(result, default=str),
            json=result,
            error=None
            if result["ok"]
            else "; ".join(e.get("error", "") for e in result["errors"])[:500],
        )
