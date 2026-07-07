"""Plan-seed handler — atomic plannable-item seeding orchestration.

Reads the seed payload from `pipeline_options` (delivered as
`context.options`), then executes a fixed sequence of cortex-api dispatch
calls:

  1. entity_create  — todo:{slug}
  2. entity_create  — plan:{slug}
  3. edge_create    — plan:{slug} --derived_from--> todo:{slug}

Per-call results are collected into a structured response object so the agent
learns exactly what succeeded in one call. No retries — partial failures stay
visible; idempotency is provided by cortex-api's unique-constraint behaviour.

The spec file (cortex://notes/system/specs/{slug}.md) is written by the /plan-seed command
before invocation — this handler is pure cortex composition, per the
architecture rationale recorded for todo-close (assertions 220, 221, 5184).
This handler does NOT call any LLM.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, override

from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput
from transport_utils import DEFAULT_CORTEX_URL, make_async_client
from universal_logging import get_logger

from ._ops import do_derived_edge, do_entity_create, extract_id, missing_keys

logger = get_logger(__name__)

_REQUEST_TIMEOUT = 15.0


class PlanSeedApplyHandler(BaseHandler):
    """Single-step handler that runs the full plannable-item seed sequence."""

    step_type = "plan_seed_apply_v1"

    @override
    async def execute(self, step: Any, context: Any) -> StepOutput:
        opts = getattr(context, "options", {}) or {}
        missing = missing_keys(opts, ("slug", "name"))
        if missing:
            err = {
                "ok": False,
                "error": f"missing required pipeline_options: {missing}",
                "expected": [
                    "slug",
                    "name",
                    "[description]",
                    "[domain]",
                    "[priority]",
                    "[agent]",
                    "[session_id]",
                ],
            }
            return StepOutput(raw=json.dumps(err), json=err, error=err["error"])

        slug = opts["slug"]
        name = opts["name"]
        description = opts.get("description")
        source_uri = f"cortex://notes/system/specs/{slug}.md"
        todo_id = f"todo:{slug}"
        plan_id = f"plan:{slug}"
        agent = opts.get("agent") or "pipeline:plan-seed"
        session_id = (
            opts.get("session_id")
            or f"plan-seed-{datetime.now(UTC).strftime('%Y-%m-%d-%H%M')}"
        )
        attributes: dict[str, Any] = {}
        if opts.get("priority"):
            attributes["priority"] = opts["priority"]
        if opts.get("domain"):
            attributes["domain"] = opts["domain"]

        result: dict[str, Any] = {
            "ok": True,
            "slug": slug,
            "todo_id": todo_id,
            "plan_id": plan_id,
            "source_uri": source_uri,
            "todo": None,
            "plan": None,
            "edge": None,
            "errors": [],
        }

        async with make_async_client(
            DEFAULT_CORTEX_URL, timeout=_REQUEST_TIMEOUT
        ) as client:
            todo_resp = await do_entity_create(
                client,
                entity_id=todo_id,
                entity_type="todo",
                name=name,
                description=description,
                source_uri=source_uri,
                attributes=attributes or None,
            )
            result["todo"] = todo_resp
            if "error" in todo_resp:
                result["ok"] = False
                result["errors"].append(
                    {"step": "entity_create:todo", "error": todo_resp["error"]}
                )

            plan_resp = await do_entity_create(
                client,
                entity_id=plan_id,
                entity_type="plan",
                name=name,
                description=description,
                source_uri=source_uri,
                attributes=attributes or None,
            )
            result["plan"] = plan_resp
            if "error" in plan_resp:
                result["ok"] = False
                result["errors"].append(
                    {"step": "entity_create:plan", "error": plan_resp["error"]}
                )

            edge_resp = await do_derived_edge(
                client,
                plan_id=plan_id,
                todo_id=todo_id,
                agent=agent,
                session_id=session_id,
            )
            result["edge"] = edge_resp
            if "error" in edge_resp:
                result["ok"] = False
                result["errors"].append(
                    {
                        "step": "edge_create:derived_from",
                        "error": edge_resp["error"],
                    }
                )
            else:
                result["edge_id"] = extract_id(edge_resp, "id", "edge_id")

        logger.info(
            "plan_seed: slug=%s todo=%s plan=%s errors=%d",
            slug,
            todo_id,
            plan_id,
            len(result["errors"]),
        )
        return StepOutput(
            raw=json.dumps(result, default=str),
            json=result,
            error=None
            if result["ok"]
            else "; ".join(e.get("error", "") for e in result["errors"])[:500],
        )
