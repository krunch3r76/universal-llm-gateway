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

logger = logging.getLogger(__name__)

_DEFAULT_REL_TYPE = "references"
_DEFAULT_EDGE_TYPE = "depends_on"
_DEPENDS_ON_CONTEXT = "Dependency closed in same cycle as todo."
_REQUEST_TIMEOUT = 15.0


def _missing(payload: dict[str, Any], keys: tuple[str, ...]) -> list[str]:
    return [k for k in keys if not payload.get(k)]


def _default_evidence(todo_id: str, agent: str | None, session_id: str | None) -> str:
    parts = [f"Closure of {todo_id}"]
    if agent:
        parts.append(f"by {agent}")
    if session_id:
        parts.append(f"in session {session_id}")
    parts.append(f"at {datetime.now(UTC).isoformat()}")
    return " ".join(parts) + "."


async def _dispatch(
    client: Any, tool: str, arguments: dict[str, Any]
) -> dict[str, Any]:
    """POST /dispatch and normalize the response into a dict.

    cortex-api `/dispatch` returns either a JSON object (success or
    structured `{"error": "..."}`) or a non-200 with a body. We surface
    everything as a dict; the caller decides whether `"error"` is present.
    """
    try:
        resp = await client.post(
            "/dispatch", json={"tool": tool, "arguments": arguments}
        )
    except Exception as exc:
        logger.warning("cortex dispatch %s failed: %s", tool, exc)
        return {"error": f"transport_error: {exc}"}
    if resp.status_code >= 400:
        try:
            body = resp.json()
            if isinstance(body, dict):
                return body if "error" in body else {"error": json.dumps(body)[:300]}
        except Exception:
            pass
        return {"error": f"http_{resp.status_code}: {resp.text[:300]}"}
    try:
        data = resp.json()
    except Exception as exc:
        return {"error": f"invalid_json_response: {exc}"}
    return data if isinstance(data, dict) else {"error": "non_object_response"}


async def _do_assert(
    client: Any,
    *,
    todo_id: str,
    summary: str,
    evidence: str,
    evidence_uris: list[str] | None,
    reasoning_summary: str | None,
    agent: str | None,
) -> dict[str, Any]:
    args: dict[str, Any] = {
        "entity_id": todo_id,
        "claim": summary,
        "confidence": "confirmed",
        "evidence": evidence,
        "derivation_type": "agent_observation",
        "confidence_score": 0.8,
    }
    if evidence_uris:
        args["evidence_uris"] = list(evidence_uris)
    if reasoning_summary:
        args["reasoning_summary"] = reasoning_summary
    if agent:
        args["seeded_by"] = agent
    return await _dispatch(client, "assert", args)


async def _do_relationship(
    client: Any,
    *,
    todo_id: str,
    item: dict[str, Any],
    agent: str | None,
    session_id: str | None,
) -> dict[str, Any]:
    target = item.get("target")
    if not target:
        return {"error": "references[].target is required"}
    args: dict[str, Any] = {
        "source_id": todo_id,
        "target_id": target,
        "type_id": item.get("type_id") or _DEFAULT_REL_TYPE,
    }
    for k in ("role", "evidence", "strength"):
        if item.get(k) is not None:
            args[k] = item[k]
    if agent:
        args["agent"] = agent
    if session_id:
        args["session_id"] = session_id
    return await _dispatch(client, "relationship_create", args)


async def _do_edge(
    client: Any,
    *,
    from_node: str,
    to_node: str,
    edge_type: str,
    context_text: str | None,
    strength: float | None,
    agent: str,
    session_id: str,
) -> dict[str, Any]:
    args: dict[str, Any] = {
        "session_id": session_id,
        "agent": agent,
        "from_node": from_node,
        "to_node": to_node,
        "edge_type": edge_type,
    }
    if context_text:
        args["context"] = context_text
    if strength is not None:
        args["strength"] = strength
    return await _dispatch(client, "edge_create", args)


async def _do_workflow_update(client: Any, todo_id: str) -> dict[str, Any]:
    return await _dispatch(
        client,
        "entity_update",
        {"entity_id": todo_id, "workflow_state": "done"},
    )


def _extract_id(result: dict[str, Any], *keys: str) -> Any:
    """Pull an id out of a dispatch result, tolerant of nested shapes."""
    for k in keys:
        if k in result:
            return result[k]
    inner = result.get("data") or result.get("item")
    if isinstance(inner, dict):
        for k in keys:
            if k in inner:
                return inner[k]
    return None


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

        missing = _missing(opts, ("todo_id", "summary"))
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

        evidence = opts.get("evidence_text") or _default_evidence(
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
            assertion_resp = await _do_assert(
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
                aid = _extract_id(assertion_resp, "id", "assertion_id")
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
                rel_resp = await _do_relationship(
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
                    rid = _extract_id(rel_resp, "id", "relationship_id")
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
                edge_resp = await _do_edge(
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
                    eid = _extract_id(edge_resp, "id", "edge_id")
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
                edge_resp = await _do_edge(
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
                    eid = _extract_id(edge_resp, "id", "edge_id")
                    if isinstance(eid, int):
                        result["edge_ids"].append(eid)

            if not skip_workflow_update:
                wf_resp = await _do_workflow_update(client, todo_id)
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
