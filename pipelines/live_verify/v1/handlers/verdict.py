"""live_verify verdict handler — probe manage, write one assertion."""

from __future__ import annotations

import json
import logging
from typing import Any, override

from manage_readonly_jsonrpc import fleet_liveness
from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput
from transport_utils import DEFAULT_CORTEX_URL, make_async_client

from ._ops import (
    acceptance_lines_from_description,
    normalize_lines,
    relations_from_snapshot,
    ruling_attributes,
    verdict_attributes,
    verdict_claim,
)

logger = logging.getLogger(__name__)

_REQUEST_TIMEOUT = 30.0


async def _dispatch(
    client: Any, tool: str, arguments: dict[str, Any]
) -> dict[str, Any]:
    try:
        resp = await client.post(
            "/dispatch", json={"tool": tool, "arguments": arguments}
        )
    except Exception as exc:
        return {"error": f"transport_error: {exc}"}
    try:
        data = resp.json()
    except Exception as exc:
        return {"error": f"invalid_json_response: {exc}"}
    if getattr(resp, "status_code", 200) >= 400:
        if isinstance(data, dict) and "error" in data:
            return data
        return {"error": f"http_{resp.status_code}"}
    return data if isinstance(data, dict) else {"error": "non_object_response"}


def _active_verdict(assertions: list[Any]) -> dict[str, Any] | None:
    found: dict[str, Any] | None = None
    for row in assertions:
        if not isinstance(row, dict) or row.get("superseded_by") is not None:
            continue
        attrs = row.get("attributes")
        if isinstance(attrs, str):
            try:
                attrs = json.loads(attrs)
            except json.JSONDecodeError:
                continue
        if isinstance(attrs, dict) and attrs.get("kind") == "live_verify":
            if found is not None:
                return None
            found = {**row, "attributes": attrs}
    return found


class LiveVerifyVerdictHandler(BaseHandler):
    """Record one live-verify assertion. Does not set workflow_state."""

    step_type = "live_verify_verdict_v1"

    @override
    async def execute(self, step: Any, context: Any) -> StepOutput:
        opts = getattr(context, "options", {}) or {}
        todo_id = opts.get("todo_id")
        land_sha = opts.get("land_sha")
        services = opts.get("services")
        if (
            not todo_id
            or not land_sha
            or not isinstance(services, list)
            or not services
        ):
            err = {
                "ok": False,
                "error": "missing required pipeline_options: todo_id, land_sha, services",
            }
            return StepOutput(raw=json.dumps(err), json=err, error=err["error"])

        probe_call = {
            "jsonrpc": "2.0",
            "method": "fleet_liveness",
            "params": {"code_ref": land_sha, "services": services},
        }
        async with make_async_client(
            DEFAULT_CORTEX_URL, timeout=_REQUEST_TIMEOUT
        ) as client:
            entity = await _dispatch(
                client, "entity_get", {"entity_id": todo_id, "intent": "full"}
            )
            if "error" in entity:
                err = {"ok": False, "error": entity["error"]}
                return StepOutput(
                    raw=json.dumps(err), json=err, error=str(err["error"])
                )

            assertions = entity.get("assertions") or []
            active = _active_verdict(assertions if isinstance(assertions, list) else [])
            ruling = opts.get("operator_ruling")
            if isinstance(ruling, str) and ruling.strip():
                if active is None:
                    err = {"ok": False, "error": "live_verify.verdict_missing"}
                    return StepOutput(raw=json.dumps(err), json=err, error=err["error"])
                attrs = ruling_attributes(active["attributes"], ruling.strip())
                claim = verdict_claim(attrs["lines"])
                written = await _dispatch(
                    client,
                    "supersede",
                    {
                        "old_assertion_id": active["id"],
                        "entity_id": todo_id,
                        "claim": claim,
                        "confidence": "confirmed",
                        "evidence": "operator_ruling supersede on the live-verify verdict",
                        "attributes": attrs,
                        "seeded_by": "pipeline:live-verify",
                    },
                )
            else:
                raw_lines = opts.get("lines")
                if not isinstance(raw_lines, list):
                    description = entity.get("description") or ""
                    raw_lines = acceptance_lines_from_description(str(description))
                snapshot = fleet_liveness(
                    code_ref=str(land_sha), services=list(services)
                )
                service_relations = relations_from_snapshot(snapshot, list(services))
                observed = {
                    "service_relations": service_relations,
                    "probe_error": snapshot.get("error"),
                }
                lines = normalize_lines(
                    raw_lines,
                    probe_call=probe_call,
                    observed=observed,
                )
                if not lines:
                    err = {"ok": False, "error": "no acceptance lines"}
                    return StepOutput(raw=json.dumps(err), json=err, error=err["error"])
                attrs = verdict_attributes(
                    land_sha=str(land_sha),
                    services=list(services),
                    lines=lines,
                    service_relations=service_relations,
                )
                claim = verdict_claim(lines)
                if active is None:
                    written = await _dispatch(
                        client,
                        "assert",
                        {
                            "entity_id": todo_id,
                            "claim": claim,
                            "confidence": "confirmed",
                            "evidence": "live_verify verdict probe",
                            "attributes": attrs,
                            "seeded_by": "pipeline:live-verify",
                        },
                    )
                else:
                    written = await _dispatch(
                        client,
                        "supersede",
                        {
                            "old_assertion_id": active["id"],
                            "entity_id": todo_id,
                            "claim": claim,
                            "confidence": "confirmed",
                            "evidence": "live_verify verdict probe supersede",
                            "attributes": attrs,
                            "seeded_by": "pipeline:live-verify",
                        },
                    )

        if "error" in written:
            err = {"ok": False, "error": written["error"], "lines": attrs.get("lines")}
            return StepOutput(
                raw=json.dumps(err, default=str), json=err, error=str(written["error"])
            )
        result = {
            "ok": True,
            "todo_id": todo_id,
            "lines": attrs["lines"],
            "service_relations": attrs.get("service_relations"),
            "assertion": written,
            "operator_ruling": attrs.get("operator_ruling"),
        }
        return StepOutput(raw=json.dumps(result, default=str), json=result, error=None)
